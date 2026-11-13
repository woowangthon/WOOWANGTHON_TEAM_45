// Safe Home — 자연어 조건 해석 + 특약 설명 API (Vercel 서버리스 함수)
//
// 11/12 회의 결정:
//   - Gemini 키는 브라우저가 아니라 이 서버리스 함수에만 둔다 (Vercel 환경 변수 GEMINI_API_KEY).
//   - 호출 횟수 제한·입력 길이 제한·오류 처리를 둔다.
//
// 요청:  POST /api/parse   {"text": "정문 10분, 월세 40 이하, 세탁기"}
// 응답:  200 {"ok": true, "fields": {...}}            ← 조건만 돌려줌. 검색·점수는 브라우저의 규칙 코드가 함
//
// 11/13 회의: 특약 설명 (계약 점검 화면의 「AI로 특약 설명 듣기」)
// 요청:  POST /api/parse   {"task": "specials", "clauses": ["특약 문장", ...]}   (1~8개, 문장마다 200자)
// 응답:  200 {"ok": true, "items": [{index, concern, category, quote, explain, question}]}
//        AI는 판정하지 않음. 인용은 원문에 있어야 하고, 안전·효력을 단정하는 설명은 버림
//
// 오류:  4xx/5xx {"ok": false, "error": "...", "message": "화면에 띄울 안내"}
// 두 작업은 같은 호출 제한(아래)을 함께 써요.
//
// 환경 변수 (Vercel → Settings → Environment Variables)
//   GEMINI_API_KEY          필수. Google AI Studio에서 발급
//   GEMINI_MODEL            선택. 기본 gemini-3.5-flash-lite (2.5 계열은 새 계정에서 404)
//
// 호출 제한 — Gemini 무료 한도(11/12 AI Studio 확인: RPM 15 · TPM 25만 · RPD 500)에 여유를 두고 맞춤
//   LIMIT_GLOBAL_PER_MINUTE 서비스 전체 분당 호출.       기본 12      (Google RPM 15의 80%)
//   LIMIT_TOKENS_PER_MINUTE 서비스 전체 분당 토큰.       기본 200000  (Google TPM 25만의 80%)
//   LIMIT_PER_DAY_TOTAL     서비스 전체 하루 호출.       기본 450     (Google RPD 500의 90%)
//   LIMIT_PER_MINUTE        한 사용자(IP) 분당 호출.     기본 3       (한 사람이 분당 한도를 다 쓰지 않게)
//   LIMIT_PER_DAY_IP        한 사용자(IP) 하루 호출.     기본 30
//   MAX_OUTPUT_TOKENS       한 번 답의 최대 토큰.        기본 256     (조건 JSON은 100토큰 안팎)
//   한도를 바꾸면(유료 전환 등) 위 숫자를 Vercel 환경 변수로 덮어쓰고 Redeploy 하면 돼요.
//
// 주의: 아래 제한은 함수 인스턴스 메모리에 저장하는 "최소한의 방어"예요. Vercel이 인스턴스를
// 새로 띄우거나 여러 개 띄우면 숫자가 나뉘거나 초기화될 수 있어요. 그래서 Google이 429(한도 초과)를
// 주면 1분 동안 Gemini 호출을 멈추고 규칙 해석으로 대신해요. 정확히 세려면 Upstash Redis 같은 저장소를 붙여요.

const MODEL = process.env.GEMINI_MODEL || "gemini-3.5-flash-lite";
const MAX_CHARS = 200;
const TIMEOUT_MS = 15000;
const env = (name, fallback) => {
  const v = Number(process.env[name]);
  return Number.isFinite(v) && v > 0 ? v : fallback;
};
const LIMITS = {
  globalPerMinute: env("LIMIT_GLOBAL_PER_MINUTE", 12),
  tokensPerMinute: env("LIMIT_TOKENS_PER_MINUTE", 200_000),
  perDayTotal: env("LIMIT_PER_DAY_TOTAL", 450),
  perMinute: env("LIMIT_PER_MINUTE", 3),
  perDayIp: env("LIMIT_PER_DAY_IP", 30),
  maxOutputTokens: env("MAX_OUTPUT_TOKENS", 256),
  cooldownMs: 60_000,
};

const GATES = ["정문", "중문", "후문"];
const KINDS = ["원룸", "투룸", "오피스텔"];
const OPTIONS = ["세탁기", "냉장고", "에어컨", "인덕션", "침대", "책상"];
const FACTORS = ["가격", "거리", "관리비", "넓이", "채광", "옵션", "조용함"];

// ── 호출 제한 (인스턴스 메모리) ──────────────────────────────
const usage = { day: "", total: 0, ips: new Map(), recent: [], tokens: [], blockedUntil: 0 };

function today(now = Date.now()) {
  // 한국 날짜 기준으로 하루를 센다 (Google RPD는 태평양 시간 자정에 초기화돼서 여유를 둠)
  return new Date(now + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

// 한 번 호출에 쓸 토큰 어림값: 프롬프트+입력 글자 수(한국어는 대략 글자당 1토큰 이하) + 최대 출력
function estimateTokens(text, task = "conditions") {
  const t = TASKS[task];
  return t.prompt.length + text.length + t.maxOutputTokens;
}

function checkLimit(ip, text = "", now = Date.now(), task = "conditions") {
  const d = today(now);
  if (usage.day !== d) {
    usage.day = d;
    usage.total = 0;
    usage.ips.clear();
  }
  usage.recent = usage.recent.filter((t) => now - t < 60_000);
  usage.tokens = usage.tokens.filter((e) => now - e.t < 60_000);
  const est = estimateTokens(text, task);

  if (now < usage.blockedUntil) return "cooldown";
  if (usage.total >= LIMITS.perDayTotal) return "daily_total";
  if (usage.recent.length >= LIMITS.globalPerMinute) return "global_minute";
  const usedTokens = usage.tokens.reduce((a, e) => a + e.n, 0);
  if (usedTokens + est > LIMITS.tokensPerMinute) return "global_tokens";

  const rec = usage.ips.get(ip) || { day: 0, recent: [] };
  rec.recent = rec.recent.filter((t) => now - t < 60_000);
  if (rec.day >= LIMITS.perDayIp) return "daily_ip";
  if (rec.recent.length >= LIMITS.perMinute) return "per_minute";

  rec.day += 1;
  rec.recent.push(now);
  usage.ips.set(ip, rec);
  usage.total += 1;
  usage.recent.push(now);
  const entry = { t: now, n: est }; // 응답이 오면 실제 사용량으로 바꿈
  usage.tokens.push(entry);
  return { entry };
}

// Gemini 응답의 실제 토큰 수로 기록을 고친다
function recordTokens(entry, actual) {
  if (entry && Number.isFinite(actual) && actual > 0) entry.n = actual;
}

// Google이 한도 초과(429)를 주면 1분 동안 Gemini를 부르지 않는다
function startCooldown(now = Date.now()) {
  usage.blockedUntil = now + LIMITS.cooldownMs;
}

// 화면의 「검색 제한」 칸에 보여 줄 숫자 (ip를 주면 그 사람 사용량도 함께)
function limitStatus(now = Date.now(), ip = null) {
  const rec = ip ? usage.ips.get(ip) : null;
  const sameDay = usage.day === today(now);
  return {
    my_today_used: rec && sameDay ? rec.day : 0,
    my_today_limit: LIMITS.perDayIp,
    my_minute_used: rec ? rec.recent.filter((t) => now - t < 60_000).length : 0,
    my_minute_limit: LIMITS.perMinute,
    today_used: sameDay ? usage.total : 0,
    today_limit: LIMITS.perDayTotal,
    minute_used: usage.recent.filter((t) => now - t < 60_000).length,
    minute_limit: LIMITS.globalPerMinute,
    minute_tokens: usage.tokens.filter((e) => now - e.t < 60_000).reduce((a, e) => a + e.n, 0),
    token_limit: LIMITS.tokensPerMinute,
    cooldown_sec: Math.max(0, Math.ceil((usage.blockedUntil - now) / 1000)),
  };
}

const LIMIT_MESSAGES = {
  per_minute: "검색을 너무 자주 했어요. 1분 뒤에 다시 시도해 주세요.",
  daily_ip: "오늘 문장 검색 횟수를 다 썼어요. 「조건 직접 고르기」로 찾아 주세요.",
  global_minute: "지금 이용자가 많아요. 간단 해석으로 대신 찾을게요.",
  global_tokens: "지금 이용자가 많아요. 간단 해석으로 대신 찾을게요.",
  cooldown: "지금 이용자가 많아요. 간단 해석으로 대신 찾을게요.",
  daily_total: "오늘 문장 검색 한도에 도달했어요. 간단 해석으로 대신 찾을게요.",
};

// ── Gemini 호출 ──────────────────────────────────────────────
const PROMPT = `너는 충북대 근처 자취방 검색 조건을 뽑는 도우미야.
사용자 문장에서 "명시적으로 말한 조건"만 JSON으로 뽑아. 말하지 않은 값은 절대 추측하지 말고 null(배열은 빈 배열)로 둬.

- max_rent_manwon: 월세 상한(만 원, 정수). "40 이하", "40만 원까지" → 40
- max_deposit_manwon: 보증금 상한(만 원, 정수). "1천" → 1000
- max_walk_min: 학교 문까지 도보 시간 상한(분, 정수). "걸어서 10분" → 10
- gate: 기준 문. ${GATES.join("/")} 중 하나. 문을 말하지 않았으면 null
- kinds: 집 유형. ${KINDS.join("/")} 중에서만
- options: 꼭 있어야 하는 옵션. ${OPTIONS.join("/")} 중에서만
- priorities: 중요하게 여기는 것. ${FACTORS.join("/")} 중에서만, 문장에 나온 순서대로.
  예) 조용한/소음 적은 → 조용함, 싼/저렴/가성비 → 가격, 학교 가까운 → 거리, 넓은 → 넓이, 밝은/햇빛 → 채광, 관리비 적은 → 관리비

사용자 문장:
`;

const SCHEMA = {
  type: "OBJECT",
  properties: {
    max_rent_manwon: { type: "INTEGER", nullable: true },
    max_deposit_manwon: { type: "INTEGER", nullable: true },
    max_walk_min: { type: "INTEGER", nullable: true },
    gate: { type: "STRING", enum: GATES, nullable: true },
    kinds: { type: "ARRAY", items: { type: "STRING", enum: KINDS } },
    options: { type: "ARRAY", items: { type: "STRING", enum: OPTIONS } },
    priorities: { type: "ARRAY", items: { type: "STRING", enum: FACTORS } },
  },
  required: ["max_rent_manwon", "max_deposit_manwon", "max_walk_min", "gate", "kinds", "options", "priorities"],
};

// ── 특약 설명 (11/13 회의: AI는 확인할 문구 후보 + 쉬운 설명 + 질문. 안전·효력은 판단하지 않음) ──
const MAX_CLAUSES = 8;
const CATEGORIES = ["전입·주민등록", "보증금 반환", "대출·담보", "수리·관리", "원상복구", "중도 해지·위약금",
  "관리비·비용", "생활 규칙", "계약 연장·변경", "기타"];
const SPECIALS_PROMPT = `너는 한국 주택 월세 계약서의 특약을 처음 자취하는 대학생에게 쉽게 설명하는 도우미야.
아래 번호가 붙은 특약 문장 하나하나에 대해 답해. 규칙:
- index: 특약 번호 (0부터)
- concern: 세입자가 계약 전에 확인하거나 물어봐야 할 내용이 있으면 true, 흔한 생활 규칙이거나 세입자를 보호하는 문장이면 false
- category: 정해진 목록 중 하나
- quote: 확인이 필요한 부분을 특약 원문에서 글자 그대로 복사 (원문에 없는 글자를 쓰지 말 것, 30자 이내). concern이 false면 빈 문자열
- explain: 쉬운 한국어 1~2문장. 이 문장이 세입자에게 무슨 뜻인지 설명. "무효다", "불법이다", "사기다", "안전하다", "문제없다"처럼 법적 효력이나 안전을 단정하지 말 것
- question: 집주인이나 중개사에게 물어볼 질문 1문장. concern이 false면 빈 문자열
특약:
`;
const SPECIALS_SCHEMA = {
  type: "OBJECT",
  properties: {
    items: {
      type: "ARRAY",
      items: {
        type: "OBJECT",
        properties: {
          index: { type: "INTEGER" },
          concern: { type: "BOOLEAN" },
          category: { type: "STRING", enum: CATEGORIES },
          quote: { type: "STRING" },
          explain: { type: "STRING" },
          question: { type: "STRING" },
        },
        required: ["index", "concern", "category", "quote", "explain", "question"],
      },
    },
  },
  required: ["items"],
};

// 특약 답 검사: 번호가 맞고, 인용이 원문에 있고, 단정 표현이 없는 것만 남긴다 (브라우저에서 한 번 더 검사)
const BANNED = /(무효(입니다|이에요|예요|다)|불법(입니다|이에요|이다)|사기(입니다|예요|다)|안전(합니다|해요|하다)|문제(가)?\s*없(습니다|어요|다)|효력이\s*없(습니다|어요|다)|유효(합니다|해요|하다))/;
function sanitizeSpecials(o, clauses) {
  const out = [];
  const seen = new Set();
  for (const it of Array.isArray(o?.items) ? o.items : []) {
    const i = it?.index;
    if (!Number.isInteger(i) || i < 0 || i >= clauses.length || seen.has(i)) continue;
    const explain = typeof it.explain === "string" ? it.explain.trim().slice(0, 200) : "";
    if (!explain || BANNED.test(explain)) continue;
    let quote = typeof it.quote === "string" ? it.quote.trim().slice(0, 60) : "";
    if (quote && !clauses[i].includes(quote)) quote = "";
    let question = typeof it.question === "string" ? it.question.trim().slice(0, 150) : "";
    if (BANNED.test(question)) question = "";
    seen.add(i);
    out.push({ index: i, concern: it.concern === true, category: CATEGORIES.includes(it.category) ? it.category : "기타",
      quote, explain, question });
  }
  return out;
}

const TASKS = {
  conditions: { prompt: PROMPT, schema: SCHEMA, maxOutputTokens: LIMITS.maxOutputTokens,
    clean: (o) => ({ fields: sanitize(o) }) },
  specials: { prompt: SPECIALS_PROMPT, schema: SPECIALS_SCHEMA, maxOutputTokens: 1024,
    clean: (o, clauses) => ({ items: sanitizeSpecials(o, clauses) }) },
};

async function callGemini(text, key, fetchImpl = fetch, task = "conditions", clauses = []) {
  const t = TASKS[task];
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetchImpl(
      `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-goog-api-key": key },
        body: JSON.stringify({
          contents: [{ role: "user", parts: [{ text: t.prompt + text }] }],
          generationConfig: {
            temperature: 0,
            responseMimeType: "application/json",
            responseSchema: t.schema,
            maxOutputTokens: t.maxOutputTokens,
          },
        }),
        signal: ctrl.signal,
      },
    );
    if (res.status === 429) return { error: "gemini_quota" };
    if (!res.ok) return { error: "gemini_http_" + res.status };
    const data = await res.json();
    const raw = data?.candidates?.[0]?.content?.parts?.[0]?.text;
    const tokens = data?.usageMetadata?.totalTokenCount;
    if (!raw) return { error: "gemini_empty", tokens };
    return { ...t.clean(JSON.parse(raw), clauses), tokens };
  } catch (e) {
    return { error: e.name === "AbortError" ? "gemini_timeout" : "gemini_failed" };
  } finally {
    clearTimeout(timer);
  }
}

// Gemini 답을 그대로 믿지 않고 허용된 값만 남긴다 (브라우저 쪽에서 한 번 더 검사함)
function sanitize(o) {
  const int = (v, lo, hi) => (Number.isInteger(v) && v >= lo && v <= hi ? v : null);
  const pick = (arr, allowed) => [...new Set((Array.isArray(arr) ? arr : []).filter((x) => allowed.includes(x)))];
  return {
    max_rent_manwon: int(o?.max_rent_manwon, 1, 500),
    max_deposit_manwon: int(o?.max_deposit_manwon, 1, 100000),
    max_walk_min: int(o?.max_walk_min, 1, 120),
    gate: GATES.includes(o?.gate) ? o.gate : null,
    kinds: pick(o?.kinds, KINDS),
    options: pick(o?.options, OPTIONS),
    priorities: pick(o?.priorities, FACTORS),
  };
}

const ERROR_MESSAGES = {
  no_key: "AI 키가 아직 설정되지 않았어요. 규칙 해석으로 대신할게요.",
  gemini_quota: "지금 이용자가 많아요. 간단 해석으로 대신 찾을게요.",
  gemini_timeout: "AI 응답이 늦어요. 규칙 해석으로 대신할게요.",
};

function send(res, status, body) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(body));
}

async function readBody(req) {
  if (req.body !== undefined) return typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body;
  let s = "";
  for await (const chunk of req) {
    s += chunk;
    if (s.length > 8000) throw new Error("too_large");
  }
  return JSON.parse(s || "{}");
}

async function handler(req, res, deps = {}) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return send(res, 204, {});
  const ip = String(req.headers?.["x-forwarded-for"] || req.socket?.remoteAddress || "unknown").split(",")[0].trim();
  if (req.method === "GET") return send(res, 200, { ok: true, model: MODEL, limits: limitStatus(Date.now(), ip) }); // 화면의 「검색 제한」
  if (req.method !== "POST") return send(res, 405, { ok: false, error: "method", message: "POST만 받아요." });

  let body;
  try {
    body = await readBody(req);
  } catch {
    return send(res, 400, { ok: false, error: "bad_json", message: "요청 형식이 잘못됐어요." });
  }
  const task = body?.task === "specials" ? "specials" : "conditions";
  let text = "";
  let clauses = [];
  if (task === "specials") {
    // 특약: 문장 1~8개, 각 200자 이내
    clauses = Array.isArray(body?.clauses) ? body.clauses.map((c) => (typeof c === "string" ? c.trim() : "")) : [];
    if (!clauses.length || clauses.some((c) => !c))
      return send(res, 400, { ok: false, error: "empty", message: "설명할 특약이 없어요." });
    if (clauses.length > MAX_CLAUSES || clauses.some((c) => c.length > MAX_CHARS))
      return send(res, 413, { ok: false, error: "too_long", message: `특약은 ${MAX_CLAUSES}개, 문장마다 ${MAX_CHARS}자까지 설명할 수 있어요.` });
    text = clauses.map((c, i) => `${i}. ${c}`).join("\n");
  } else {
    text = typeof body?.text === "string" ? body.text.trim() : "";
    if (!text) return send(res, 400, { ok: false, error: "empty", message: "문장을 입력해 주세요." });
    if (text.length > MAX_CHARS)
      return send(res, 413, { ok: false, error: "too_long", message: `문장은 ${MAX_CHARS}자 이내로 써 주세요.` });
  }

  const key = deps.key ?? process.env.GEMINI_API_KEY;
  if (!key) return send(res, 503, { ok: false, error: "no_key", message: ERROR_MESSAGES.no_key });

  const allowed = checkLimit(ip, text, Date.now(), task);
  if (typeof allowed === "string")
    return send(res, 429, { ok: false, error: allowed, message: LIMIT_MESSAGES[allowed], limits: limitStatus(Date.now(), ip) });

  const out = await callGemini(text, key, deps.fetch, task, clauses);
  recordTokens(allowed.entry, out.tokens);
  if (out.error === "gemini_quota") startCooldown();
  if (out.error) {
    const msg = ERROR_MESSAGES[out.error] || "AI 분석에 실패했어요. 규칙 해석으로 대신할게요.";
    return send(res, out.error === "gemini_quota" ? 429 : 502,
      { ok: false, error: out.error, message: msg, limits: limitStatus(Date.now(), ip) });
  }
  const limits = limitStatus(Date.now(), ip);
  if (task === "specials") return send(res, 200, { ok: true, model: MODEL, items: out.items, limits });
  return send(res, 200, { ok: true, model: MODEL, fields: out.fields, limits });
}

module.exports = handler;
module.exports._test = { checkLimit, recordTokens, startCooldown, limitStatus, estimateTokens, sanitize, sanitizeSpecials, usage, LIMITS, TASKS };
