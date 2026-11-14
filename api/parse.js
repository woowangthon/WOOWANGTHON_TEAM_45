// Safe Home — 자연어 조건 해석 + 특약 설명 API (Vercel 서버리스 함수)
//
// 11/12 회의 결정:
//   - Gemini 키는 브라우저가 아니라 이 서버리스 함수에만 둔다 (Vercel 환경 변수 GEMINI_API_KEY).
//   - 호출 횟수 제한·입력 길이 제한·오류 처리를 둔다.
//
// 요청:  POST /api/parse   {"text": "정문 10분, 월세 40 이하, 세탁기"}
// 응답:  200 {"ok": true, "fields": {...}}            ← 조건만 돌려줌. 검색·점수는 브라우저의 규칙 코드가 함
//
// 11/13 회의: 특약 설명 (계약 점검 화면의 「AI 특약 설명」)
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
//   한도를 바꾸면(유료 전환 등) 위 숫자를 Vercel 환경 변수로 덮어쓰고 Redeploy 하면 됩니다.
//
// 사용량 저장소 (11/14 Redis 연동)
//   KV_REST_API_URL / KV_REST_API_TOKEN          Vercel Marketplace의 Upstash Redis를 연결하면 자동으로 생김
//   (또는 UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN — Upstash에서 직접 만든 경우)
//   REDIS_KEY_PREFIX   선택. Redis 키 앞부분. 기본 "safehome" (같은 Redis를 다른 프로젝트와 나눠 쓸 때 바꿈)
//   REDIS_REQUIRED     선택. "1"이면 Redis 설정이 없을 때도 Gemini를 부르지 않음(운영에서 Redis가 꼭 있어야 할 때)
//
//   - Redis가 설정돼 있으면: 모든 함수 인스턴스가 같은 카운터를 써요. 검사와 증가는 Lua 스크립트 하나로
//     원자적으로 해서 동시에 요청이 와도 한도를 넘지 않습니다. Google 429 뒤 1분 쉼(cooldown)도 Redis에 둬서
//     모든 인스턴스가 함께 쉬어요(Google 한도는 키 하나에 걸리므로 공유해야 함).
//   - Redis 오류·지연(2초)이면 Gemini를 부르지 않고 429 {error:"limit_unavailable"}로 답해요.
//     화면은 기존처럼 규칙 해석으로 대신해요(안전하게 막힘).
//   - Redis 설정이 아예 없으면(연결 전): 예전처럼 인스턴스 메모리로 세요. 연결 전에 배포돼도 사이트가 멈추지 않게.
//   응답 헤더 X-RateLimit-Store: redis | memory 로 지금 어느 쪽인지 확인할 수 있습니다(비밀 값은 안 보임).
//
// Redis 키 (모두 만료가 있어 저절로 지워짐, 날짜는 한국 시간 기준)
//   {prefix}:day:{YYYY-MM-DD}:total        하루 전체 호출 수     — 2일 뒤 만료 (날짜가 바뀌면 새 키 = 자정 초기화)
//   {prefix}:day:{YYYY-MM-DD}:ip:{해시}     하루 IP별 호출 수     — 2일 뒤 만료 (IP는 SHA-256 해시로만 저장)
//   {prefix}:min:global / :min:ip:{해시}    최근 60초 호출 시각   — 2분 뒤 만료, 60초 지난 기록은 매번 정리
//   {prefix}:min:tok / :min:tokn           최근 60초 토큰(시각·양) — 2분 뒤 만료
//   {prefix}:cooldown                      Google 429 뒤 60초 쉼 — 60초 뒤 만료

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

// ── 호출 제한 (인스턴스 메모리) — Redis 설정이 없을 때만 씀 ───────────
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

// ── 호출 제한 (Redis, 여러 인스턴스 공유) ─────────────────────────
const crypto = require("crypto");
const WINDOW_MS = 60_000;
const DAY_TTL_SEC = 2 * 24 * 3600;
const MINUTE_TTL_MS = 120_000;
const REDIS_TIMEOUT_MS = 2000;

function redisConfig(e = process.env) {
  const url = e.KV_REST_API_URL || e.UPSTASH_REDIS_REST_URL;
  const token = e.KV_REST_API_TOKEN || e.UPSTASH_REDIS_REST_TOKEN;
  return url && token ? { url: url.replace(/\/+$/, ""), token, prefix: e.REDIS_KEY_PREFIX || "safehome" } : null;
}

function ipHash(ip) {
  return crypto.createHash("sha256").update(String(ip)).digest("hex").slice(0, 20);
}

function redisKeys(prefix, ip, now) {
  const d = today(now);
  const h = ipHash(ip);
  return [
    `${prefix}:cooldown`, `${prefix}:day:${d}:total`, `${prefix}:day:${d}:ip:${h}`,
    `${prefix}:min:global`, `${prefix}:min:ip:${h}`, `${prefix}:min:tok`, `${prefix}:min:tokn`,
  ];
}

// 검사 + 증가를 한 번에 (Redis는 Lua 스크립트를 한 번에 하나씩 실행하므로 동시 요청에도 한도를 넘지 않음)
// 확인 순서는 메모리 방식과 같음: cooldown → 하루 전체 → 분당 전체 → 분당 토큰 → 하루 IP → 분당 IP
const ACQUIRE_LUA = `
local now = tonumber(ARGV[1])
local cut = now - tonumber(ARGV[2])
if redis.call('EXISTS', KEYS[1]) == 1 then return {'cooldown'} end
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', cut)
redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', cut)
local old = redis.call('ZRANGEBYSCORE', KEYS[6], '-inf', cut)
for _, m in ipairs(old) do redis.call('HDEL', KEYS[7], m) end
redis.call('ZREMRANGEBYSCORE', KEYS[6], '-inf', cut)
if tonumber(redis.call('GET', KEYS[2]) or '0') >= tonumber(ARGV[3]) then return {'daily_total'} end
if redis.call('ZCARD', KEYS[4]) >= tonumber(ARGV[4]) then return {'global_minute'} end
local used = 0
for _, v in ipairs(redis.call('HVALS', KEYS[7])) do used = used + tonumber(v) end
if used + tonumber(ARGV[9]) > tonumber(ARGV[5]) then return {'global_tokens'} end
if tonumber(redis.call('GET', KEYS[3]) or '0') >= tonumber(ARGV[6]) then return {'daily_ip'} end
if redis.call('ZCARD', KEYS[5]) >= tonumber(ARGV[7]) then return {'per_minute'} end
redis.call('INCR', KEYS[2]); redis.call('EXPIRE', KEYS[2], tonumber(ARGV[11]))
redis.call('INCR', KEYS[3]); redis.call('EXPIRE', KEYS[3], tonumber(ARGV[11]))
redis.call('ZADD', KEYS[4], now, ARGV[8]); redis.call('PEXPIRE', KEYS[4], tonumber(ARGV[12]))
redis.call('ZADD', KEYS[5], now, ARGV[8]); redis.call('PEXPIRE', KEYS[5], tonumber(ARGV[12]))
redis.call('ZADD', KEYS[6], now, ARGV[8]); redis.call('PEXPIRE', KEYS[6], tonumber(ARGV[12]))
redis.call('HSET', KEYS[7], ARGV[8], ARGV[9]); redis.call('PEXPIRE', KEYS[7], tonumber(ARGV[12]))
return {'ok', ARGV[10]}
`;

// Gemini가 알려 준 실제 토큰 수로 고침 (60초가 지나 이미 정리됐으면 그대로 둠)
const TOKENS_LUA = `
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then redis.call('HSET', KEYS[1], ARGV[1], ARGV[2]) end
return 1
`;

// 화면의 「검색 제한」 숫자 (읽기만 함)
const STATUS_LUA = `
local cut = tonumber(ARGV[1]) - tonumber(ARGV[2])
local used = 0
local ids = redis.call('ZRANGEBYSCORE', KEYS[6], '(' .. cut, '+inf')
for _, m in ipairs(ids) do used = used + tonumber(redis.call('HGET', KEYS[7], m) or '0') end
return {
  tonumber(redis.call('GET', KEYS[2]) or '0'), tonumber(redis.call('GET', KEYS[3]) or '0'),
  redis.call('ZCOUNT', KEYS[4], '(' .. cut, '+inf'), redis.call('ZCOUNT', KEYS[5], '(' .. cut, '+inf'),
  used, redis.call('PTTL', KEYS[1])
}
`;

async function redisCall(cfg, command, fetchImpl = fetch) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REDIS_TIMEOUT_MS);
  try {
    const res = await fetchImpl(cfg.url, {
      method: "POST",
      headers: { Authorization: `Bearer ${cfg.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(command),
      signal: ctrl.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error !== undefined) throw new Error("redis_error"); // 비밀 값·응답 내용은 남기지 않음
    return data.result;
  } finally {
    clearTimeout(timer);
  }
}

const redisLimiter = {
  async check(cfg, ip, text, now, task, fetchImpl) {
    const id = crypto.randomUUID();
    const est = estimateTokens(text, task);
    const r = await redisCall(cfg, ["EVAL", ACQUIRE_LUA, "7", ...redisKeys(cfg.prefix, ip, now),
      String(now), String(WINDOW_MS), String(LIMITS.perDayTotal), String(LIMITS.globalPerMinute),
      String(LIMITS.tokensPerMinute), String(LIMITS.perDayIp), String(LIMITS.perMinute), id, String(est),
      today(now), String(DAY_TTL_SEC), String(MINUTE_TTL_MS)], fetchImpl);
    if (!Array.isArray(r)) throw new Error("redis_error");
    return r[0] === "ok" ? { entry: { id, now, ip } } : String(r[0]);
  },
  async record(cfg, entry, actual, fetchImpl) {
    if (!entry || !Number.isFinite(actual) || actual <= 0) return;
    const keys = redisKeys(cfg.prefix, entry.ip, entry.now);
    await redisCall(cfg, ["EVAL", TOKENS_LUA, "1", keys[6], entry.id, String(Math.round(actual))], fetchImpl);
  },
  async cooldown(cfg, fetchImpl) {
    await redisCall(cfg, ["SET", `${cfg.prefix}:cooldown`, "1", "PX", String(LIMITS.cooldownMs)], fetchImpl);
  },
  async status(cfg, ip, now, fetchImpl) {
    const r = await redisCall(cfg, ["EVAL", STATUS_LUA, "7", ...redisKeys(cfg.prefix, ip, now),
      String(now), String(WINDOW_MS)], fetchImpl);
    const [total, mine, minute, myMinute, tokens, pttl] = (r || []).map(Number);
    return {
      my_today_used: mine || 0,
      my_today_limit: LIMITS.perDayIp,
      my_minute_used: myMinute || 0,
      my_minute_limit: LIMITS.perMinute,
      today_used: total || 0,
      today_limit: LIMITS.perDayTotal,
      minute_used: minute || 0,
      minute_limit: LIMITS.globalPerMinute,
      minute_tokens: tokens || 0,
      token_limit: LIMITS.tokensPerMinute,
      cooldown_sec: pttl > 0 ? Math.ceil(pttl / 1000) : 0,
    };
  },
};

// 메모리 방식도 같은 모양으로 감싼다 (Redis 연결 전·로컬 테스트용)
const memoryLimiter = {
  async check(cfg, ip, text, now, task) { return checkLimit(ip, text, now, task); },
  async record(cfg, entry, actual) { recordTokens(entry, actual); },
  async cooldown(cfg, fetchImpl, now = Date.now()) { startCooldown(now); },
  async status(cfg, ip, now) { return limitStatus(now, ip); },
};

const LIMIT_MESSAGES = {
  per_minute: "검색을 너무 자주 했습니다. 1분 뒤에 다시 시도하십시오.",
  daily_ip: "오늘 문장 검색 횟수를 모두 사용했습니다. 「세부 조건」으로 검색하십시오.",
  global_minute: "지금 이용자가 많습니다. 간단 해석으로 검색합니다.",
  global_tokens: "지금 이용자가 많습니다. 간단 해석으로 검색합니다.",
  cooldown: "지금 이용자가 많습니다. 간단 해석으로 검색합니다.",
  daily_total: "오늘 문장 검색 한도에 도달했습니다. 간단 해석으로 검색합니다.",
  limit_unavailable: "지금은 AI 사용량을 확인할 수 없습니다. 간단 해석으로 검색합니다.",
};

// ── Gemini 호출 ──────────────────────────────────────────────
const PROMPT = `너는 충북대 근처 자취방 검색 조건을 뽑는 도우미야.
사용자 문장에서 "명시적으로 말한 조건"만 JSON으로 뽑아. 말하지 않은 값은 절대 추측하지 말고 null(배열은 빈 배열)로 둬.

- max_rent_manwon: 월세 상한(만 원, 정수). "40 이하", "40만 원까지" → 40. "30~40" → 40. "30 이상"처럼 하한만 말하면 null
- min_rent_manwon: 월세 하한(만 원, 정수). "30 이상", "30~40" → 30. 하한을 말하지 않았으면 null
- max_deposit_manwon: 보증금 상한(만 원, 정수). "1천" → 1000
- max_walk_min: 학교 문까지 도보 시간 상한(분, 정수). "걸어서 10분" → 10
- gate: 기준 문. ${GATES.join("/")} 중 하나. 문을 말하지 않았으면 null
- kinds: 집 유형. ${KINDS.join("/")} 중에서만
- options: 필수 옵션. ${OPTIONS.join("/")} 중에서만
- priorities: 중요하게 여기는 것. ${FACTORS.join("/")} 중에서만, 문장에 나온 순서대로.
  예) 조용한/소음 적은 → 조용함, 싼/저렴/가성비 → 가격, 학교 가까운 → 거리, 넓은 → 넓이, 밝은/햇빛 → 채광, 관리비 적은 → 관리비

사용자 문장:
`;

const SCHEMA = {
  type: "OBJECT",
  properties: {
    max_rent_manwon: { type: "INTEGER", nullable: true },
    min_rent_manwon: { type: "INTEGER", nullable: true },
    max_deposit_manwon: { type: "INTEGER", nullable: true },
    max_walk_min: { type: "INTEGER", nullable: true },
    gate: { type: "STRING", enum: GATES, nullable: true },
    kinds: { type: "ARRAY", items: { type: "STRING", enum: KINDS } },
    options: { type: "ARRAY", items: { type: "STRING", enum: OPTIONS } },
    priorities: { type: "ARRAY", items: { type: "STRING", enum: FACTORS } },
  },
  required: ["max_rent_manwon", "min_rent_manwon", "max_deposit_manwon", "max_walk_min", "gate", "kinds", "options", "priorities"],
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
- explain: 쉬운 한국어 1~2문장. 반드시 합니다/입니다체, 감탄·이모지·해요체 금지. 이 문장이 세입자에게 무슨 뜻인지 설명. "무효다", "불법이다", "사기다", "안전하다", "문제없다"처럼 법적 효력이나 안전을 단정하지 말 것
- question: 집주인이나 중개사에게 물어볼 질문 1문장. 합니까/입니까체 사용, 해요체 금지. concern이 false면 빈 문자열
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
    min_rent_manwon: int(o?.min_rent_manwon, 1, 500),
    max_deposit_manwon: int(o?.max_deposit_manwon, 1, 100000),
    max_walk_min: int(o?.max_walk_min, 1, 120),
    gate: GATES.includes(o?.gate) ? o.gate : null,
    kinds: pick(o?.kinds, KINDS),
    options: pick(o?.options, OPTIONS),
    priorities: pick(o?.priorities, FACTORS),
  };
}

const ERROR_MESSAGES = {
  no_key: "AI 연결을 사용할 수 없습니다. 규칙 해석으로 검색합니다.",
  gemini_quota: "지금 이용자가 많습니다. 간단 해석으로 검색합니다.",
  gemini_timeout: "AI 응답이 지연되고 있습니다. 규칙 해석으로 대신합니다.",
};

// ── Origin 검사 (11/14) ─────────────────────────────────────
// 다른 웹사이트가 방문자 브라우저를 시켜 이 API(=우리 Gemini 할당량)를 쓰는 것을 막는 장치입니다.
// curl 같은 프로그램은 Origin 헤더를 마음대로 넣을 수 있어서 인증 수단은 아닙니다. 남용은 위의 호출 제한이 막아요.
// 정확히 같은 주소만 허용하고 *.vercel.app 같은 넓은 와일드카드는 쓰지 않습니다.
const DEFAULT_ORIGINS = ["https://woowangthon-team-45.vercel.app"];
const LOCAL_ORIGIN = /^http:\/\/(localhost|127\.0\.0\.1)(:\d{1,5})?$/;

function allowedOrigins(e = process.env) {
  const set = new Set(DEFAULT_ORIGINS);
  for (const name of ["VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"]) {
    const host = (e[name] || "").trim();
    if (host) set.add(host.startsWith("http") ? host.replace(/\/+$/, "") : `https://${host}`);
  }
  for (const o of (e.ALLOWED_ORIGINS || "").split(",")) {
    const v = o.trim().replace(/\/+$/, "");
    if (v && v !== "*" && !v.includes("*")) set.add(v);
  }
  return set;
}

function originAllowed(origin, e = process.env) {
  return Boolean(origin) && (allowedOrigins(e).has(origin) || LOCAL_ORIGIN.test(origin));
}

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
  const envs = deps.env || process.env;
  const origin = req.headers?.origin;
  res.setHeader("Vary", "Origin");
  const okOrigin = originAllowed(origin, envs);
  if (okOrigin) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  }
  const forbidden = () => send(res, 403, { ok: false, error: "origin", message: "허용되지 않은 사이트에서 온 요청입니다." });
  if (req.method === "OPTIONS") return okOrigin ? send(res, 204, {}) : forbidden();
  // GET: Origin이 없으면(같은 사이트·주소창) 허용, 다른 사이트 Origin이면 거부
  // POST: Origin이 반드시 있어야 하고 허용 목록에 있어야 함 (브라우저는 POST에 항상 Origin을 붙임)
  if (origin && !okOrigin) return forbidden();
  if (req.method === "POST" && !origin) return forbidden();

  const now = deps.now ? deps.now() : Date.now();
  const cfg = deps.redis !== undefined ? deps.redis : redisConfig(envs);
  const required = String(envs.REDIS_REQUIRED || "") === "1";
  const limiter = cfg ? redisLimiter : memoryLimiter;
  res.setHeader("X-RateLimit-Store", cfg ? "redis" : "memory");
  const rf = deps.redisFetch;
  const ip = String(req.headers?.["x-forwarded-for"] || req.socket?.remoteAddress || "unknown").split(",")[0].trim();
  const status = async () => {
    try { return await limiter.status(cfg, ip, deps.now ? deps.now() : Date.now(), rf); } catch { return undefined; }
  };
  if (req.method === "GET") {
    if (!cfg && required) return send(res, 503, { ok: false, error: "limit_unavailable", message: LIMIT_MESSAGES.limit_unavailable });
    const limits = await status();
    if (!limits) return send(res, 503, { ok: false, error: "limit_unavailable", message: LIMIT_MESSAGES.limit_unavailable });
    return send(res, 200, { ok: true, model: MODEL, limits }); // 화면의 「검색 제한」
  }
  if (req.method !== "POST") return send(res, 405, { ok: false, error: "method", message: "POST만 받습니다." });

  let body;
  try {
    body = await readBody(req);
  } catch {
    return send(res, 400, { ok: false, error: "bad_json", message: "요청 형식이 올바르지 않습니다." });
  }
  const task = body?.task === "specials" ? "specials" : "conditions";
  let text = "";
  let clauses = [];
  if (task === "specials") {
    // 특약: 문장 1~8개, 각 200자 이내
    clauses = Array.isArray(body?.clauses) ? body.clauses.map((c) => (typeof c === "string" ? c.trim() : "")) : [];
    if (!clauses.length || clauses.some((c) => !c))
      return send(res, 400, { ok: false, error: "empty", message: "설명할 특약이 없습니다." });
    if (clauses.length > MAX_CLAUSES || clauses.some((c) => c.length > MAX_CHARS))
      return send(res, 413, { ok: false, error: "too_long", message: `특약은 ${MAX_CLAUSES}개, 문장마다 ${MAX_CHARS}자까지 설명할 수 있습니다.` });
    text = clauses.map((c, i) => `${i}. ${c}`).join("\n");
  } else {
    text = typeof body?.text === "string" ? body.text.trim() : "";
    if (!text) return send(res, 400, { ok: false, error: "empty", message: "문장을 입력하십시오." });
    if (text.length > MAX_CHARS)
      return send(res, 413, { ok: false, error: "too_long", message: `문장은 ${MAX_CHARS}자 이내로 입력하십시오.` });
  }

  const key = deps.key ?? process.env.GEMINI_API_KEY;
  if (!key) return send(res, 503, { ok: false, error: "no_key", message: ERROR_MESSAGES.no_key });

  // 사용량 저장소를 확인할 수 없으면 Gemini를 부르지 않는다 (제한 없이 부르는 일이 없게)
  const unavailable = () => send(res, 429, { ok: false, error: "limit_unavailable", message: LIMIT_MESSAGES.limit_unavailable });
  if (!cfg && required) return unavailable();
  let allowed;
  try {
    allowed = await limiter.check(cfg, ip, text, now, task, rf);
  } catch {
    return unavailable();
  }
  if (typeof allowed === "string")
    return send(res, 429, { ok: false, error: allowed, message: LIMIT_MESSAGES[allowed], limits: await status() });

  const out = await callGemini(text, key, deps.fetch, task, clauses);
  try {
    await limiter.record(cfg, allowed.entry, out.tokens, rf);
    if (out.error === "gemini_quota") await limiter.cooldown(cfg, rf, deps.now ? deps.now() : Date.now());
  } catch {
    // 기록 실패는 응답을 막지 않음 (다음 요청의 검사에서 Redis 장애면 다시 막힘)
  }
  if (out.error) {
    const msg = ERROR_MESSAGES[out.error] || "AI 분석에 실패했습니다. 규칙 해석으로 대신합니다.";
    return send(res, out.error === "gemini_quota" ? 429 : 502,
      { ok: false, error: out.error, message: msg, limits: await status() });
  }
  const limits = await status();
  if (task === "specials") return send(res, 200, { ok: true, model: MODEL, items: out.items, limits });
  return send(res, 200, { ok: true, model: MODEL, fields: out.fields, limits });
}

module.exports = handler;
module.exports._test = { checkLimit, recordTokens, startCooldown, limitStatus, estimateTokens, sanitize, sanitizeSpecials, usage, LIMITS, TASKS,
  redisConfig, redisLimiter, originAllowed, allowedOrigins, ipHash };
