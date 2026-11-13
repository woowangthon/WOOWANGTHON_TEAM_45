// api/parse.js 테스트 — 실행: node test_api.js
//
// - Gemini는 흉내만 내요(실제 호출·할당량 사용 없음).
// - Redis는 컴퓨터에 redis-server가 있으면 진짜로 띄우고, Upstash REST API처럼 답하는 작은 중계 서버를 붙여서
//   Lua 스크립트까지 실제로 실행해 봐요. redis-server가 없으면 Redis 테스트는 건너뛰어요.
// - 함수 인스턴스 여러 개는 모듈을 새로 불러와서(메모리 따로) 흉내 내요.
"use strict";
const assert = require("assert");
const http = require("http");
const net = require("net");
const path = require("path");
const { spawn, spawnSync } = require("child_process");

const MOD = path.join(__dirname, "api", "parse.js");
const ORIGIN = "https://woowangthon-team-45.vercel.app";
const KST_DAY1_2359 = Date.UTC(2026, 10, 14, 14, 59, 0); // 한국 11/14 23:59
const KST_DAY2_0001 = Date.UTC(2026, 10, 14, 15, 1, 0);  // 한국 11/15 00:01

function freshInstance() {
  delete require.cache[require.resolve(MOD)];
  return require(MOD);
}

function mkRes() {
  const r = { headers: {}, statusCode: 0, body: "" };
  r.setHeader = (k, v) => (r.headers[k.toLowerCase()] = v);
  r.end = (s) => (r.body = s);
  r.json = () => JSON.parse(r.body || "{}");
  return r;
}

function req({ method = "POST", body, ip = "1.1.1.1", origin = ORIGIN } = {}) {
  const headers = { "x-forwarded-for": ip };
  if (origin) headers.origin = origin;
  return { method, body, headers };
}

let geminiCalls = 0;
function fakeGemini(status = 200, tokens = 500, payload = { max_rent_manwon: 30 }) {
  return async () => {
    geminiCalls += 1;
    return {
      status, ok: status < 300,
      json: async () => ({ usageMetadata: { totalTokenCount: tokens },
        candidates: [{ content: { parts: [{ text: JSON.stringify(payload) }] } }] }),
    };
  };
}

async function call(inst, opts, deps) {
  const r = mkRes();
  await inst(req(opts), r, { key: "K", env: {}, ...deps });
  return r;
}

// ── Redis + Upstash REST 흉내 서버 ───────────────────────────
function respEncode(args) {
  return `*${args.length}\r\n` + args.map((a) => { const b = Buffer.from(String(a)); return `$${b.length}\r\n${a}\r\n`; }).join("");
}
function respParse(buf, i = 0) {
  const t = String.fromCharCode(buf[i]);
  const end = buf.indexOf("\r\n", i);
  if (end < 0) return null;
  const line = buf.slice(i + 1, end).toString();
  if (t === "+" ) return [line, end + 2];
  if (t === "-") return [{ error: line }, end + 2];
  if (t === ":") return [Number(line), end + 2];
  if (t === "$") {
    const n = Number(line);
    if (n < 0) return [null, end + 2];
    if (buf.length < end + 2 + n + 2) return null;
    return [buf.slice(end + 2, end + 2 + n).toString(), end + 2 + n + 2];
  }
  if (t === "*") {
    const n = Number(line);
    if (n < 0) return [null, end + 2];
    const out = [];
    let j = end + 2;
    for (let k = 0; k < n; k++) {
      const r = respParse(buf, j);
      if (!r) return null;
      out.push(r[0]);
      j = r[1];
    }
    return [out, j];
  }
  throw new Error("bad resp " + t);
}
function redisCommand(port, args) {
  return new Promise((resolve, reject) => {
    const s = net.connect(port, "127.0.0.1");
    let buf = Buffer.alloc(0);
    s.on("data", (d) => {
      buf = Buffer.concat([buf, d]);
      const r = respParse(buf);
      if (r) { s.end(); resolve(r[0]); }
    });
    s.on("error", reject);
    s.write(respEncode(args));
  });
}
async function startRedis() {
  if (spawnSync("which", ["redis-server"]).status !== 0) return null;
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn("redis-server", ["--port", String(port), "--save", "", "--appendonly", "no"], { stdio: "ignore" });
  for (let i = 0; i < 50; i++) {
    try { if ((await redisCommand(port, ["PING"])) === "PONG") break; } catch { /* 아직 안 뜸 */ }
    await new Promise((r) => setTimeout(r, 100));
  }
  // Upstash REST처럼: POST / , Authorization: Bearer 토큰, 본문 = 명령 배열 → {"result": ...}
  const TOKEN = "test-token";
  let calls = 0;
  const server = http.createServer((rq, rs) => {
    let body = "";
    rq.on("data", (c) => (body += c));
    rq.on("end", async () => {
      calls += 1;
      if (rq.headers.authorization !== `Bearer ${TOKEN}`) { rs.writeHead(401); return rs.end(JSON.stringify({ error: "Unauthorized" })); }
      try {
        const out = await redisCommand(port, JSON.parse(body));
        if (out && out.error) { rs.writeHead(400); return rs.end(JSON.stringify({ error: out.error })); }
        rs.writeHead(200, { "Content-Type": "application/json" });
        rs.end(JSON.stringify({ result: out }));
      } catch (e) { rs.writeHead(500); rs.end(JSON.stringify({ error: String(e) })); }
    });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const url = `http://127.0.0.1:${server.address().port}`;
  return {
    cfg: (prefix) => ({ url, token: TOKEN, prefix }),
    flush: () => redisCommand(port, ["FLUSHALL"]),
    keys: () => redisCommand(port, ["KEYS", "*"]),
    ttl: (k) => redisCommand(port, ["TTL", k]),
    calls: () => calls,
    stop: () => { server.close(); proc.kill(); },
  };
}

(async () => {
  let r;
  // ── 1. Origin 검사 ─────────────────────────────────────────
  {
    const h = freshInstance();
    const ok = fakeGemini();
    r = await call(h, { body: { text: "월세 30" } }, { fetch: ok });
    assert.equal(r.statusCode, 200);
    assert.equal(r.headers["access-control-allow-origin"], ORIGIN, "허용된 Origin만 그대로 돌려줌");
    assert.equal(r.headers["vary"], "Origin");
    for (const bad of ["https://evil.example", "https://woowangthon-team-45.vercel.app.evil.com", "https://x.vercel.app", "null"]) {
      const before = geminiCalls;
      r = await call(h, { body: { text: "월세 30" }, origin: bad }, { fetch: ok });
      assert.equal(r.statusCode, 403, bad);
      assert.equal(geminiCalls, before, "차단된 Origin은 Gemini를 부르지 않음");
      assert.equal(r.headers["access-control-allow-origin"], undefined);
    }
    r = await call(h, { body: { text: "월세 30" }, origin: null }, { fetch: ok });
    assert.equal(r.statusCode, 403, "Origin 없는 POST 거부");
    r = await call(h, { method: "GET", origin: null }, {});
    assert.equal(r.statusCode, 200, "Origin 없는 GET 허용");
    r = await call(h, { method: "GET", origin: "https://evil.example" }, {});
    assert.equal(r.statusCode, 403, "다른 사이트 GET 거부");
    r = await call(h, { method: "OPTIONS" }, {});
    assert.equal(r.statusCode, 204);
    r = await call(h, { method: "OPTIONS", origin: "https://evil.example" }, {});
    assert.equal(r.statusCode, 403);
    for (const local of ["http://localhost:8501", "http://127.0.0.1:3000", "http://localhost"]) {
      r = await call(h, { body: { text: "월세 30" }, origin: local, ip: "9.9.9." + local.length }, { fetch: ok });
      assert.equal(r.statusCode, 200, local);
    }
    // Vercel이 넣어 주는 주소와 ALLOWED_ORIGINS
    const env = { VERCEL_URL: "safehome-abc123.vercel.app", VERCEL_BRANCH_URL: "safehome-git-x.vercel.app",
      ALLOWED_ORIGINS: "https://safehome-test-gules.vercel.app, https://*.vercel.app" };
    const { originAllowed } = h._test;
    assert(originAllowed("https://safehome-abc123.vercel.app", env));
    assert(originAllowed("https://safehome-git-x.vercel.app", env));
    assert(originAllowed("https://safehome-test-gules.vercel.app", env));
    assert(!originAllowed("https://other.vercel.app", env), "와일드카드는 무시");
    assert(!originAllowed("https://safehome-test-gules.vercel.app", {}), "ALLOWED_ORIGINS 없으면 개인 사이트는 기본 허용 안 함");
    console.log("1 Origin 검사 ✅ (허용·차단·없음·OPTIONS·localhost·VERCEL_URL·ALLOWED_ORIGINS)");
  }

  // ── 2. Redis 설정이 없으면 예전 메모리 방식 그대로 (연결 전 배포해도 안전) ─────
  {
    const h = freshInstance();
    const ok = fakeGemini();
    for (let i = 0; i < 3; i++) assert.equal((await call(h, { body: { text: "월세 30" }, ip: "2.2.2.2" }, { fetch: ok })).statusCode, 200);
    r = await call(h, { body: { text: "월세 30" }, ip: "2.2.2.2" }, { fetch: ok });
    assert.equal(r.json().error, "per_minute");
    assert.equal(r.headers["x-ratelimit-store"], "memory");
    const before = geminiCalls;
    r = await call(h, { body: { text: "월세 30" }, ip: "2.2.2.3" }, { fetch: ok, env: { REDIS_REQUIRED: "1" } });
    assert.equal(r.statusCode, 429);
    assert.equal(r.json().error, "limit_unavailable", "REDIS_REQUIRED=1인데 설정이 없으면 막음");
    assert.equal(geminiCalls, before);
    console.log("2 Redis 미설정 → 메모리 방식 유지, REDIS_REQUIRED=1이면 차단 ✅");
  }

  // ── 3. Redis 장애 → Gemini 호출 차단 ───────────────────────
  {
    const h = freshInstance();
    const dead = { url: "http://127.0.0.1:1", token: "x", prefix: "t" };
    const before = geminiCalls;
    r = await call(h, { body: { text: "월세 30" } }, { fetch: fakeGemini(), redis: dead });
    assert.equal(r.statusCode, 429);
    assert.equal(r.json().error, "limit_unavailable");
    assert(r.json().message.includes("간단 해석"), "화면이 규칙 해석으로 넘어가는 문구");
    assert.equal(geminiCalls, before, "Redis 장애면 Gemini를 부르지 않음");
    r = await call(h, { method: "GET" }, { redis: dead });
    assert.equal(r.statusCode, 503, "사용량 조회 실패는 503 (화면은 '불러오지 못함' 표시)");
    // 응답이 늦는 Redis (2초 넘으면 끊음)
    const slow = async (url, opts) => new Promise((_, rej) => opts.signal.addEventListener("abort", () => rej(new Error("abort"))));
    const t0 = Date.now();
    r = await call(h, { body: { text: "월세 30" } }, { fetch: fakeGemini(), redis: { url: "http://x", token: "x", prefix: "t" }, redisFetch: slow });
    assert.equal(r.json().error, "limit_unavailable");
    assert(Date.now() - t0 < 3500, "2초 안팎에서 끊음");
    // Redis가 오류를 돌려줄 때
    const errFetch = async () => ({ ok: false, status: 400, json: async () => ({ error: "ERR" }) });
    r = await call(h, { body: { text: "월세 30" } }, { fetch: fakeGemini(), redis: { url: "http://x", token: "x", prefix: "t" }, redisFetch: errFetch });
    assert.equal(r.json().error, "limit_unavailable");
    console.log("3 Redis 연결 실패·지연·오류 → Gemini 호출 차단, 규칙 해석 안내 ✅");
  }

  // ── 4~9. 실제 Redis ────────────────────────────────────────
  const R = await startRedis();
  if (!R) {
    console.log("redis-server가 없어 실제 Redis 테스트는 건너뜀");
  } else {
    try {
      const ok = fakeGemini(200, 500);
      const now = () => KST_DAY1_2359;

      // 4. 연결 성공 + 인스턴스 2개가 같은 카운터 공유 (IP 분당 3번)
      await R.flush();
      let A = freshInstance(), B = freshInstance();
      const cfg = R.cfg("t4");
      const d = { fetch: ok, redis: cfg, now };
      assert.equal((await call(A, { body: { text: "월세 30" }, ip: "4.4.4.4" }, d)).statusCode, 200);
      assert.equal((await call(B, { body: { text: "월세 30" }, ip: "4.4.4.4" }, d)).statusCode, 200);
      r = await call(A, { body: { text: "월세 30" }, ip: "4.4.4.4" }, d);
      assert.equal(r.statusCode, 200);
      assert.equal(r.headers["x-ratelimit-store"], "redis");
      r = await call(B, { body: { text: "월세 30" }, ip: "4.4.4.4" }, d);
      assert.equal(r.json().error, "per_minute", "다른 인스턴스에서도 같은 IP 분당 한도");
      r = await call(B, { method: "GET", ip: "4.4.4.4", origin: null }, d);
      assert.equal(r.json().limits.my_minute_used, 3);
      assert.equal(r.json().limits.today_used, 3);
      assert.equal(r.json().limits.minute_tokens, 1500, "실제 토큰 수(500×3)로 기록");
      const keys = await R.keys();
      assert(keys.every((k) => !k.includes("4.4.4.4")), "IP는 원문이 아니라 해시로 저장");
      for (const k of keys) assert((await R.ttl(k)) > 0, "모든 키에 만료가 있음: " + k);
      console.log("4 Redis 연결 · 인스턴스 간 공유 · 실제 토큰 반영 · IP 해시 · 키 만료 ✅");

      // 5. 동시에 30개 → 전체 분당 12번을 넘지 않음 (인스턴스 3개, IP 30개)
      await R.flush();
      const insts = [freshInstance(), freshInstance(), freshInstance()];
      const c5 = { fetch: ok, redis: R.cfg("t5"), now };
      const results = await Promise.all(Array.from({ length: 30 }, (_, i) =>
        call(insts[i % 3], { body: { text: "월세 30" }, ip: `5.5.5.${i}` }, c5)));
      const passed = results.filter((x) => x.statusCode === 200).length;
      assert.equal(passed, 12, `동시 요청 통과 수 ${passed} (한도 12)`);
      assert(results.filter((x) => x.statusCode === 429).every((x) => x.json().error === "global_minute"));
      console.log("5 동시 요청 30개 → 정확히 12개만 통과 (원자적 검사) ✅");

      // 6. 조건 분석 + 특약 설명이 같은 한도를 나눠 씀
      await R.flush();
      A = freshInstance();
      const c6 = { fetch: ok, redis: R.cfg("t6"), now };
      assert.equal((await call(A, { body: { text: "월세 30" }, ip: "6.6.6.6" }, c6)).statusCode, 200);
      const sp = fakeGemini(200, 700, { items: [] });
      assert.equal((await call(A, { body: { task: "specials", clauses: ["반려동물은 키울 수 없다."] }, ip: "6.6.6.6" }, { ...c6, fetch: sp })).statusCode, 200);
      assert.equal((await call(A, { body: { text: "월세 30" }, ip: "6.6.6.6" }, c6)).statusCode, 200);
      r = await call(A, { body: { task: "specials", clauses: ["반려동물은 키울 수 없다."] }, ip: "6.6.6.6" }, { ...c6, fetch: sp });
      assert.equal(r.json().error, "per_minute", "조건+특약 합쳐서 IP 분당 3번");
      console.log("6 조건 분석 + 특약 설명 사용량 합산 ✅");

      // 7. 분당 토큰 한도 (실제 토큰이 크게 나온 뒤 다음 요청 막힘)
      await R.flush();
      A = freshInstance();
      const big = fakeGemini(200, 199_500); // 199,500 + 다음 요청 어림 약 820 > 200,000
      const c7 = { redis: R.cfg("t7"), now };
      assert.equal((await call(A, { body: { text: "월세 30" }, ip: "7.0.0.1" }, { ...c7, fetch: big })).statusCode, 200);
      r = await call(A, { body: { text: "월세 30" }, ip: "7.0.0.2" }, { ...c7, fetch: ok });
      assert.equal(r.json().error, "global_tokens");
      // 60초가 지나면 다시 됨
      r = await call(A, { body: { text: "월세 30" }, ip: "7.0.0.2" }, { ...c7, fetch: ok, now: () => KST_DAY1_2359 + 61_000 });
      assert.equal(r.statusCode, 200);
      console.log("7 분당 토큰 20만 · 60초 뒤 풀림 ✅");

      // 8. Gemini 429 → 모든 인스턴스가 60초 쉼
      await R.flush();
      A = freshInstance(); B = freshInstance();
      const c8 = { redis: R.cfg("t8"), now };
      r = await call(A, { body: { text: "월세 30" }, ip: "8.0.0.1" }, { ...c8, fetch: fakeGemini(429) });
      assert.equal(r.json().error, "gemini_quota");
      const before = geminiCalls;
      r = await call(B, { body: { text: "월세 30" }, ip: "8.0.0.2" }, { ...c8, fetch: ok });
      assert.equal(r.json().error, "cooldown", "다른 인스턴스도 쉼");
      assert.equal(geminiCalls, before);
      assert(r.json().limits.cooldown_sec > 0 && r.json().limits.cooldown_sec <= 60);
      console.log("8 Gemini 429 → cooldown 60초 인스턴스 간 공유 ✅");

      // 9. 하루 한도와 자정(한국 시간) 초기화
      await R.flush();
      A = freshInstance();
      const c9 = { fetch: ok, redis: R.cfg("t9") };
      const { LIMITS } = A._test;
      for (let i = 0; i < LIMITS.perDayIp; i++) {
        const t = KST_DAY1_2359 - (LIMITS.perDayIp - i) * 61_000; // 분당 한도에 안 걸리게 1분씩 띄움
        assert.equal((await call(A, { body: { text: "월세 30" }, ip: "9.9.9.9" }, { ...c9, now: () => t })).statusCode, 200);
      }
      r = await call(A, { body: { text: "월세 30" }, ip: "9.9.9.9" }, { ...c9, now: () => KST_DAY1_2359 });
      assert.equal(r.json().error, "daily_ip", "IP 하루 30번");
      r = await call(A, { body: { text: "월세 30" }, ip: "9.9.9.9" }, { ...c9, now: () => KST_DAY2_0001 });
      assert.equal(r.statusCode, 200, "한국 자정이 지나면 새 날로 초기화");
      // 전체 하루 450번: 카운터를 직접 채워서 확인
      await R.flush();
      const dayKey = `t9b:day:2026-11-14:total`;
      await redisCommand_(R, ["SET", dayKey, String(LIMITS.perDayTotal)]);
      r = await call(A, { body: { text: "월세 30" }, ip: "9.9.9.8" }, { fetch: ok, redis: R.cfg("t9b"), now: () => KST_DAY1_2359 });
      assert.equal(r.json().error, "daily_total");
      console.log("9 하루 한도(IP 30·전체 450) · 한국 자정 초기화 ✅");

      // 10. GET 사용량 조회 형식이 예전과 같음
      r = await call(A, { method: "GET", origin: null }, { redis: R.cfg("t9b"), now: () => KST_DAY1_2359 });
      assert.deepEqual(Object.keys(r.json().limits).sort(), ["cooldown_sec", "minute_limit", "minute_tokens", "minute_used",
        "my_minute_limit", "my_minute_used", "my_today_limit", "my_today_used", "today_limit", "today_used", "token_limit"]);
      assert.equal(r.json().limits.today_used, LIMITS.perDayTotal);
      console.log("10 GET 사용량 조회 형식 그대로 ✅");
    } finally {
      R.stop();
    }
  }
  console.log("\nAPI 테스트 모두 통과 ✅ (Gemini 실제 호출 0번)");
})().catch((e) => { console.error(e); process.exit(1); });

// 테스트 9에서 Redis에 직접 값을 넣을 때 씀
function redisCommand_(R, args) {
  return fetch(R.cfg("x").url, { method: "POST", headers: { Authorization: "Bearer test-token" }, body: JSON.stringify(args) });
}
