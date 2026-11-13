// Safe Home — 자연어 조건 해석 API (Vercel 서버리스 함수)
//
// 11/12 회의 결정:
//   - Gemini 키는 브라우저가 아니라 이 서버리스 함수에만 둔다 (Vercel 환경 변수 GEMINI_API_KEY).
//   - 호출 횟수 제한·입력 길이 제한·오류 처리를 둔다.
//
// 요청:  POST /api/parse   {"text": "정문 10분, 월세 40 이하, 세탁기"}
// 응답:  200 {"ok": true, "fields": {...}}            ← 조건만 돌려줌. 검색·점수는 브라우저의 규칙 코드가 함
//        4xx/5xx {"ok": false, "error": "...", "message": "화면에 띄울 안내"}
//
// 환경 변수 (Vercel → Settings → Environment Variables)
//   GEMINI_API_KEY        필수. Google AI Studio에서 발급
//   GEMINI_MODEL          선택. 기본 gemini-3.5-flash-lite (2.5 계열은 새 계정에서 404)
//   LIMIT_PER_MINUTE      선택. 한 사용자(IP) 분당 최대 호출. 기본 5
//   LIMIT_PER_DAY_IP      선택. 한 사용자(IP) 하루 최대 호출. 기본 30
//   LIMIT_PER_DAY_TOTAL   선택. 전체 하루 최대 호출. 기본 200
//                         → AI Studio에서 확인한 RPD(하루 최대 요청)보다 작게 잡는다.
//
// 주의: 아래 횟수 제한은 함수 인스턴스 메모리에 저장하는 "최소한의 방어"예요. Vercel이 인스턴스를
// 새로 띄우면 숫자가 초기화될 수 있어요. 정확한 제한이 필요하면 Upstash Redis 같은 저장소를 붙여요.
// 최종 안전장치는 Google 쪽 할당량이라, 한도를 넘으면 Gemini가 429를 주고 화면은 규칙 해석으로 대체해요.

const MODEL = process.env.GEMINI_MODEL || "gemini-3.5-flash-lite";
const MAX_CHARS = 200;
const TIMEOUT_MS = 15000;
const LIMITS = {
  perMinute: Number(process.env.LIMIT_PER_MINUTE) || 5,
  perDayIp: Number(process.env.LIMIT_PER_DAY_IP) || 30,
  perDayTotal: Number(process.env.LIMIT_PER_DAY_TOTAL) || 200,
};

const GATES = ["정문", "중문", "후문"];
const KINDS = ["원룸", "투룸", "오피스텔"];
const OPTIONS = ["세탁기", "냉장고", "에어컨", "인덕션", "침대", "책상"];
const FACTORS = ["가격", "거리", "관리비", "넓이", "채광", "옵션", "조용함"];

// ── 호출 횟수 제한 (인스턴스 메모리) ─────────────────────────
const usage = { day: "", total: 0, ips: new Map() };

function today() {
  // 한국 날짜 기준으로 하루를 센다
  return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

function checkLimit(ip, now = Date.now()) {
  const d = today();
  if (usage.day !== d) {
    usage.day = d;
    usage.total = 0;
    usage.ips.clear();
  }
  if (usage.total >= LIMITS.perDayTotal) return "daily_total";
  const rec = usage.ips.get(ip) || { day: 0, recent: [] };
  rec.recent = rec.recent.filter((t) => now - t < 60_000);
  if (rec.day >= LIMITS.perDayIp) return "daily_ip";
  if (rec.recent.length >= LIMITS.perMinute) return "per_minute";
  rec.day += 1;
  rec.recent.push(now);
  usage.ips.set(ip, rec);
  usage.total += 1;
  return null;
}

const LIMIT_MESSAGES = {
  per_minute: "AI 분석을 너무 자주 눌렀어요. 1분 뒤에 다시 시도해 주세요.",
  daily_ip: "오늘 AI 분석 횟수를 다 썼어요. 아래 입력칸에서 직접 고르거나 규칙 해석을 써 주세요.",
  daily_total: "오늘 서비스 전체 AI 분석 한도에 도달했어요. 규칙 해석으로 대신할게요.",
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

async function callGemini(text, key, fetchImpl = fetch) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetchImpl(
      `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-goog-api-key": key },
        body: JSON.stringify({
          contents: [{ role: "user", parts: [{ text: PROMPT + text }] }],
          generationConfig: {
            temperature: 0,
            responseMimeType: "application/json",
            responseSchema: SCHEMA,
          },
        }),
        signal: ctrl.signal,
      },
    );
    if (res.status === 429) return { error: "gemini_quota" };
    if (!res.ok) return { error: "gemini_http_" + res.status };
    const data = await res.json();
    const raw = data?.candidates?.[0]?.content?.parts?.[0]?.text;
    if (!raw) return { error: "gemini_empty" };
    return { fields: sanitize(JSON.parse(raw)) };
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
  gemini_quota: "Gemini 사용 한도에 도달했어요. 규칙 해석으로 대신할게요.",
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
    if (s.length > 4000) throw new Error("too_large");
  }
  return JSON.parse(s || "{}");
}

async function handler(req, res, deps = {}) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return send(res, 204, {});
  if (req.method !== "POST") return send(res, 405, { ok: false, error: "method", message: "POST만 받아요." });

  let body;
  try {
    body = await readBody(req);
  } catch {
    return send(res, 400, { ok: false, error: "bad_json", message: "요청 형식이 잘못됐어요." });
  }
  const text = typeof body?.text === "string" ? body.text.trim() : "";
  if (!text) return send(res, 400, { ok: false, error: "empty", message: "문장을 입력해 주세요." });
  if (text.length > MAX_CHARS)
    return send(res, 413, { ok: false, error: "too_long", message: `문장은 ${MAX_CHARS}자 이내로 써 주세요.` });

  const key = deps.key ?? process.env.GEMINI_API_KEY;
  if (!key) return send(res, 503, { ok: false, error: "no_key", message: ERROR_MESSAGES.no_key });

  const ip = String(req.headers?.["x-forwarded-for"] || req.socket?.remoteAddress || "unknown").split(",")[0].trim();
  const limited = checkLimit(ip);
  if (limited) return send(res, 429, { ok: false, error: limited, message: LIMIT_MESSAGES[limited] });

  const out = await callGemini(text, key, deps.fetch);
  if (out.error) {
    const msg = ERROR_MESSAGES[out.error] || "AI 분석에 실패했어요. 규칙 해석으로 대신할게요.";
    return send(res, out.error === "gemini_quota" ? 429 : 502, { ok: false, error: out.error, message: msg });
  }
  return send(res, 200, { ok: true, model: MODEL, fields: out.fields });
}

module.exports = handler;
module.exports._test = { checkLimit, sanitize, usage, LIMITS };
