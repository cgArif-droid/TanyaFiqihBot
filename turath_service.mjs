import http from "node:http";
import { search, getBookInfo } from "turath-sdk";

const PORT = Number(process.env.TURATH_PORT || 8765);
const HOST = process.env.TURATH_HOST || "127.0.0.1";

const MAX_RESULTS_PER_QUERY = Math.max(1, Number(process.env.MAX_RESULTS_PER_QUERY || 8));
const MAX_FINAL_RESULTS = Math.max(1, Number(process.env.MAX_FINAL_RESULTS || 18));
const MAX_QUERIES_PER_REQUEST = Math.max(1, Number(process.env.MAX_QUERIES_PER_REQUEST || 12));
const QUERY_CONCURRENCY = Math.max(1, Number(process.env.QUERY_CONCURRENCY || 4));
const GLOBAL_SEARCH_CONCURRENCY = Math.max(1, Number(process.env.GLOBAL_SEARCH_CONCURRENCY || 6));
const BOOK_INFO_CONCURRENCY = Math.max(1, Number(process.env.BOOK_INFO_CONCURRENCY || 3));
const MAX_BOOK_INFO_CACHE = Math.max(50, Number(process.env.MAX_BOOK_INFO_CACHE || 1000));
const TURATH_DEBUG = process.env.TURATH_DEBUG === "1";
const MAX_BODY_BYTES = Math.max(16_384, Number(process.env.MAX_BODY_BYTES || 1_048_576));

const CATEGORY_IDS = Object.freeze({
  hanafi: 14,
  maliki: 15,
  shafii: 16,
  hanbali: 17,
});

const CATEGORY_NAMES_AR = Object.freeze({
  hanafi: "الحنفية",
  maliki: "المالكية",
  shafii: "الشافعية",
  hanbali: "الحنابلة",
});

const CATEGORY_ID_TO_NAME = Object.fromEntries(
  Object.entries(CATEGORY_IDS).map(([name, id]) => [String(id), name])
);

/* =========================================================
   CONCURRENCY CONTROL
========================================================= */
class Semaphore {
  constructor(limit) {
    this.limit = Math.max(1, Number(limit) || 1);
    this.active = 0;
    this.waiters = [];
  }

  async run(task) {
    // Jika penuh, slot akan dipindahkan terus kepada waiter seterusnya.
    // Waiter yang dibangunkan tidak menaikkan counter sekali lagi.
    if (this.active >= this.limit) {
      await new Promise((resolve) => this.waiters.push(resolve));
    } else {
      this.active += 1;
    }

    try {
      return await task();
    } finally {
      const next = this.waiters.shift();
      if (next) {
        next();
      } else {
        this.active = Math.max(0, this.active - 1);
      }
    }
  }
}

async function mapLimit(items, limit, worker) {
  const output = new Array(items.length);
  let cursor = 0;
  const count = Math.min(items.length, Math.max(1, Number(limit) || 1));
  await Promise.all(Array.from({ length: count }, async () => {
    while (true) {
      const index = cursor++;
      if (index >= items.length) return;
      try {
        output[index] = await worker(items[index], index);
      } catch (error) {
        console.error("[TASK ERROR]", error?.message || error);
        output[index] = null;
      }
    }
  }));
  return output;
}

const SEARCH_SEMAPHORE = new Semaphore(GLOBAL_SEARCH_CONCURRENCY);
const BOOK_INFO_SEMAPHORE = new Semaphore(BOOK_INFO_CONCURRENCY);
const BOOK_INFO_CACHE = new Map();

/* =========================================================
   HELPERS
========================================================= */
function cleanText(value) {
  if (value === undefined || value === null) return "";
  return String(value).replace(/\s+/g, " ").trim();
}

function firstValue(obj, keys = []) {
  if (!obj || typeof obj !== "object") return null;
  for (const key of keys) {
    const value = obj[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      return value;
    }
  }
  return null;
}

function findValueDeep(obj, keys = [], depth = 0, visited = new Set()) {
  if (depth > 10 || obj === null || obj === undefined || typeof obj !== "object") return "";
  if (visited.has(obj)) return "";
  visited.add(obj);

  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(obj, key)) continue;
    const value = obj[key];
    if (value === undefined || value === null) continue;
    if (typeof value === "string" && value.trim()) return cleanText(value);
    if (typeof value === "number") return value;
    if (typeof value === "object") {
      const nested = findValueDeep(value, keys, depth + 1, visited);
      if (nested !== "" && nested !== null && nested !== undefined) return nested;
    }
  }

  for (const value of Object.values(obj)) {
    if (value && typeof value === "object") {
      const nested = findValueDeep(value, keys, depth + 1, visited);
      if (nested !== "" && nested !== null && nested !== undefined) return nested;
    }
  }
  return "";
}

function findTextDeep(obj, depth = 0, visited = new Set()) {
  if (depth > 10 || obj === null || obj === undefined) return "";
  if (typeof obj === "string") {
    const text = cleanText(obj);
    return text.length >= 30 ? text : "";
  }
  if (typeof obj !== "object" || visited.has(obj)) return "";
  visited.add(obj);

  if (Array.isArray(obj)) {
    for (const item of obj) {
      const found = findTextDeep(item, depth + 1, visited);
      if (found) return found;
    }
    return "";
  }

  const preferredKeys = [
    "text", "content", "snippet", "passage", "body", "quote",
    "raw_text", "rawText", "text_content", "textContent",
    "matched_text", "excerpt", "full_text", "paragraph", "preview",
  ];
  for (const key of preferredKeys) {
    if (obj[key] === undefined || obj[key] === null) continue;
    const found = findTextDeep(obj[key], depth + 1, visited);
    if (found) return found;
  }

  const ignored = new Set([
    "title", "book", "book_name", "bookName", "author", "author_name",
    "authorName", "url", "link", "href", "metadata", "meta",
  ]);
  for (const [key, value] of Object.entries(obj)) {
    if (ignored.has(key)) continue;
    const found = findTextDeep(value, depth + 1, visited);
    if (found) return found;
  }
  return "";
}

function extractPage(obj) {
  const value = findValueDeep(obj, [
    "page", "page_number", "pageNumber", "page_no", "pageNo", "pageno", "pageNum",
  ]);
  return value === "" ? "" : value;
}

function extractBookId(obj) {
  const direct = findValueDeep(obj, ["book_id", "bookId", "bookid", "id_book", "bookID"]);
  if (direct !== "") return direct;
  if (obj && typeof obj === "object") {
    const id = firstValue(obj, ["id"]);
    if (id !== null && /^\d+$/.test(String(id).trim())) return String(id).trim();
  }
  return "";
}

function extractBook(obj) {
  if (!obj || typeof obj !== "object") return typeof obj === "string" ? cleanText(obj) : "";
  const direct = findValueDeep(obj, [
    "book_name", "bookName", "book_title", "bookTitle", "kitab_name", "kitabName",
    "name_ar", "nameAr", "title_ar", "titleAr", "book",
  ]);
  if (direct) return cleanText(direct);
  if (typeof obj.book === "string") return cleanText(obj.book);
  if (obj.book && typeof obj.book === "object") {
    return cleanText(findValueDeep(obj.book, ["name", "title", "book_name", "bookName", "name_ar", "nameAr"]));
  }
  return "";
}

function extractAuthor(obj) {
  if (!obj || typeof obj !== "object") return "";
  const direct = findValueDeep(obj, [
    "author_name", "authorName", "author_full_name", "authorFullName", "writer_name",
    "writerName", "muallif", "muallif_name", "author_ar", "authorAr", "author",
  ]);
  if (direct) return cleanText(direct);
  if (typeof obj.author === "string") return cleanText(obj.author);
  if (obj.author && typeof obj.author === "object") {
    return cleanText(findValueDeep(obj.author, ["name", "full_name", "fullName", "author_name", "authorName"]));
  }
  return "";
}

function extractUrl(obj) {
  const value = findValueDeep(obj, ["url", "link", "href", "book_url", "bookUrl", "source_url", "sourceUrl"]);
  return value ? cleanText(value) : "";
}

function buildBookUrl(bookId) {
  if (bookId === undefined || bookId === null || String(bookId).trim() === "") return "";
  return `https://app.turath.io/book/${encodeURIComponent(String(bookId))}`;
}

function normalizeCategory(category) {
  if (typeof category === "number" || /^\d+$/.test(String(category ?? ""))) {
    return CATEGORY_ID_TO_NAME[String(category)] || "shafii";
  }
  const name = cleanText(category).toLowerCase();
  return Object.hasOwn(CATEGORY_IDS, name) ? name : "shafii";
}

function extractRawItems(raw) {
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  const directKeys = ["data", "results", "items", "hits", "sources", "passages", "matches"];
  for (const key of directKeys) {
    if (Array.isArray(raw[key])) return raw[key];
  }
  if (raw.data && typeof raw.data === "object") {
    for (const key of directKeys) {
      if (Array.isArray(raw.data[key])) return raw.data[key];
    }
  }
  return [];
}

function scoreResult(result, query) {
  let score = 0;
  const text = `${result.text || ""} ${result.book || ""} ${result.author || ""}`.toLowerCase();
  const terms = cleanText(query).toLowerCase().split(/\s+/).filter((term) => term.length >= 2);
  for (const term of terms) if (text.includes(term)) score += 2;
  if ((result.text || "").length >= 200) score += 2;
  if ((result.text || "").length >= 500) score += 2;
  if (result.book) score += 2;
  if (result.author) score += 2;
  if (result.page !== "") score += 2;
  if (result.book_id) score += 1;
  if (result.url) score += 1;
  return score;
}

/* =========================================================
   BOOK INFO CACHE
========================================================= */
async function getBookInfoCached(bookId) {
  const key = String(bookId);
  if (BOOK_INFO_CACHE.has(key)) {
    const cached = BOOK_INFO_CACHE.get(key);
    // Refresh insertion order for approximate LRU behavior.
    BOOK_INFO_CACHE.delete(key);
    BOOK_INFO_CACHE.set(key, cached);
    return cached;
  }

  const promise = BOOK_INFO_SEMAPHORE.run(() => getBookInfo(bookId));
  BOOK_INFO_CACHE.set(key, promise);

  while (BOOK_INFO_CACHE.size > MAX_BOOK_INFO_CACHE) {
    const oldest = BOOK_INFO_CACHE.keys().next().value;
    if (oldest === undefined) break;
    BOOK_INFO_CACHE.delete(oldest);
  }

  try {
    return await promise;
  } catch (error) {
    if (BOOK_INFO_CACHE.get(key) === promise) BOOK_INFO_CACHE.delete(key);
    throw error;
  }
}

async function enrichBook(item, options = {}) {
  const fetchInfo = options.fetchInfo !== false;
  let book = extractBook(item);
  let author = extractAuthor(item);
  let page = extractPage(item);
  let bookId = extractBookId(item);
  let url = extractUrl(item);

  if (item?.book && typeof item.book === "object") {
    if (!book) book = extractBook(item.book);
    if (!author) author = extractAuthor(item.book);
    if (!bookId) bookId = extractBookId(item.book);
    if (!url) url = extractUrl(item.book);
  }

  if (fetchInfo && bookId && (!book || !author || page === "")) {
    try {
      const info = await getBookInfoCached(bookId);
      if (info) {
        if (TURATH_DEBUG) {
          console.log(`[DEBUG BOOK INFO ${bookId}]`, JSON.stringify(info, null, 2).slice(0, 6000));
        }
        if (!book) book = extractBook(info);
        if (!author) author = extractAuthor(info);
        if (page === "") page = extractPage(info);
        if (!url) url = extractUrl(info);
        if (info.data && typeof info.data === "object") {
          if (!book) book = extractBook(info.data);
          if (!author) author = extractAuthor(info.data);
          if (page === "") page = extractPage(info.data);
          if (!url) url = extractUrl(info.data);
        }
      }
    } catch (error) {
      console.warn(`[BOOK INFO ERROR ${bookId}]`, error?.message || error);
    }
  }

  if (!url && bookId) url = buildBookUrl(bookId);
  return {
    text: findTextDeep(item),
    book: cleanText(book),
    author: cleanText(author),
    page: page === null || page === undefined ? "" : page,
    book_id: bookId || "",
    url: cleanText(url),
    category: item?.category || item?.category_name || item?.categoryName || "",
    raw: item,
  };
}

/* =========================================================
   SDK SEARCH
========================================================= */
async function sdkSearch(query, options = {}) {
  const category = options.category ?? CATEGORY_IDS.shafii;
  return SEARCH_SEMAPHORE.run(async () => {
    try {
      if (TURATH_DEBUG) console.log(`[SDK SEARCH] query=${query}; category=${category}`);
      return await search(query, { category, page: 1 });
    } catch (error) {
      console.error(`[TURATH SEARCH ERROR] ${query}:`, error?.message || error);
      return { count: 0, data: [] };
    }
  });
}

async function searchOneQuery(query, options = {}) {
  const raw = await sdkSearch(query, options);
  const items = extractRawItems(raw).slice(0, MAX_RESULTS_PER_QUERY);
  if (TURATH_DEBUG) console.log(`[QUERY RESULT] ${query}: ${items.length}`);

  const processed = await mapLimit(items, Math.min(8, MAX_RESULTS_PER_QUERY), async (item) => {
    const normalized = await enrichBook(item, { fetchInfo: false });
    return normalized.text ? normalized : null;
  });
  return processed.filter(Boolean);
}

/* =========================================================
   MULTI SEARCH: PARALLEL QUERIES, LIMITED GLOBAL LOAD
========================================================= */
async function multiSearch(queries, options = {}) {
  const cleanQueries = [...new Set(
    (Array.isArray(queries) ? queries : []).map(cleanText).filter(Boolean)
  )].slice(0, MAX_QUERIES_PER_REQUEST);

  const groups = await mapLimit(cleanQueries, QUERY_CONCURRENCY, async (query) => {
    const results = await searchOneQuery(query, options);
    return results.map((result) => ({
      ...result,
      _query: query,
      _score: scoreResult(result, query),
    }));
  });

  const uniqueMap = new Map();
  for (const item of groups.flat()) {
    if (!item?.text) continue;
    const key = [item.book_id || item.book || "", item.page ?? "", cleanText(item.text).slice(0, 250).toLowerCase()].join("|");
    const previous = uniqueMap.get(key);
    if (!previous || item._score > previous._score) uniqueMap.set(key, item);
  }

  const candidates = [...uniqueMap.values()]
    .sort((a, b) => b._score - a._score)
    .slice(0, MAX_FINAL_RESULTS);

  // Metadata calls happen only for the final shortlist, not every search result.
  const finalEnriched = await mapLimit(candidates, BOOK_INFO_CONCURRENCY, async (candidate) => {
    if (candidate.book && candidate.author && candidate.page !== "") return candidate;
    if (!candidate.book_id) return candidate;
    try {
      const extra = await enrichBook(candidate.raw || candidate, { fetchInfo: true });
      return {
        ...candidate,
        text: extra.text || candidate.text,
        book: extra.book || candidate.book,
        author: extra.author || candidate.author,
        page: extra.page !== "" ? extra.page : candidate.page,
        book_id: extra.book_id || candidate.book_id,
        url: extra.url || candidate.url,
        category: extra.category || candidate.category,
      };
    } catch (error) {
      console.warn("[METADATA ENRICH ERROR]", error?.message || error);
      return candidate;
    }
  });

  return finalEnriched.filter(Boolean).map((item) => ({
    text: item.text || "",
    book: item.book || "",
    author: item.author || "",
    page: item.page ?? "",
    book_id: item.book_id || "",
    url: item.url || "",
    category: item.category || "",
  }));
}

/* =========================================================
   HTTP HELPERS
========================================================= */
function sendJson(res, statusCode, data) {
  const body = JSON.stringify(data);
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
  });
  res.end(body);
}

async function parseBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    let bytes = 0;
    req.on("data", (chunk) => {
      bytes += chunk.length;
      if (bytes > MAX_BODY_BYTES) {
        reject(new Error("Request body terlalu besar."));
        req.destroy();
        return;
      }
      body += chunk.toString("utf8");
    });
    req.on("end", () => {
      if (!body.trim()) return resolve({});
      try {
        resolve(JSON.parse(body));
      } catch {
        reject(new Error("JSON request tidak sah."));
      }
    });
    req.on("error", reject);
  });
}

function readQueriesFromGet(url) {
  const rawQueries = url.searchParams.get("queries");
  if (rawQueries) {
    try {
      const parsed = JSON.parse(rawQueries);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      // Falls back to q/query below.
    }
  }
  const one = url.searchParams.get("q") || url.searchParams.get("query") || "";
  return one ? [one] : [];
}

async function handleSearch(req, res, url, body = null) {
  const queries = body
    ? (Array.isArray(body.queries) ? body.queries : body.query ? [body.query] : [])
    : readQueriesFromGet(url);

  if (!queries.length) {
    return sendJson(res, 400, { ok: false, error: "query atau queries diperlukan" });
  }

  const categoryName = normalizeCategory(body?.category ?? url.searchParams.get("category") ?? "shafii");
  const categoryId = CATEGORY_IDS[categoryName];
  const results = await multiSearch(queries, { category: categoryId });

  return sendJson(res, 200, {
    ok: true,
    query: body?.query || url.searchParams.get("q") || url.searchParams.get("query") || "",
    queries,
    category: categoryName,
    category_id: categoryId,
    count: results.length,
    results,
    sources: results,
    data: results,
  });
}

/* =========================================================
   SERVER
========================================================= */
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || `${HOST}:${PORT}`}`);

    if (req.method === "GET" && url.pathname === "/health") {
      return sendJson(res, 200, {
        ok: true,
        service: "turath",
        status: "running",
        categories: CATEGORY_IDS,
        max_results_per_query: MAX_RESULTS_PER_QUERY,
        max_final_results: MAX_FINAL_RESULTS,
        max_queries_per_request: MAX_QUERIES_PER_REQUEST,
        query_concurrency: QUERY_CONCURRENCY,
        global_search_concurrency: GLOBAL_SEARCH_CONCURRENCY,
        book_info_concurrency: BOOK_INFO_CONCURRENCY,
        book_info_cache_size: BOOK_INFO_CACHE.size,
      });
    }

    if (req.method === "GET" && url.pathname === "/categories") {
      return sendJson(res, 200, { ok: true, categories: CATEGORY_IDS, names: CATEGORY_NAMES_AR });
    }

    if (req.method === "GET" && url.pathname === "/search") {
      return await handleSearch(req, res, url);
    }

    if (req.method === "POST" && url.pathname === "/search") {
      const body = await parseBody(req);
      return await handleSearch(req, res, url, body);
    }

    if (req.method === "GET" && url.pathname === "/") {
      return sendJson(res, 200, {
        ok: true,
        service: "TanyaFiqihBot Turath Service",
        version: "5.0-fast",
        categories: CATEGORY_IDS,
        max_results_per_query: MAX_RESULTS_PER_QUERY,
        max_final_results: MAX_FINAL_RESULTS,
      });
    }

    return sendJson(res, 404, { ok: false, error: "Not found" });
  } catch (error) {
    console.error("[TURATH SERVER ERROR]", error?.stack || error);
    if (!res.headersSent) {
      return sendJson(res, 500, { ok: false, error: error?.message || "Internal server error" });
    }
    res.end();
  }
});

server.listen(PORT, HOST, () => {
  console.log("==============================================");
  console.log("TURATH SERVICE STARTED");
  console.log(`Listening on http://${HOST}:${PORT}`);
  console.log("Categories:", CATEGORY_IDS);
  console.log(`Max queries/request: ${MAX_QUERIES_PER_REQUEST}`);
  console.log(`Global search concurrency: ${GLOBAL_SEARCH_CONCURRENCY}`);
  console.log(`Max final sources/category: ${MAX_FINAL_RESULTS}`);
  console.log(`Debug mode: ${TURATH_DEBUG}`);
  console.log("==============================================");
});

function shutdown(signal) {
  console.log(`[TURATH] ${signal}; closing server...`);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 10_000).unref();
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
