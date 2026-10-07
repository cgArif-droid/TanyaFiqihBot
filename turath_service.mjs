import http from "http";
import { search, getBookInfo, getPage } from "turath-sdk";

const PORT = Number(process.env.TURATH_PORT || 8765);
const HOST = process.env.TURATH_HOST || "127.0.0.1";

const CATEGORY_IDS = {
  hanafi: 14,
  maliki: 15,
  shafii: 16,
  hanbali: 17,
};

const CATEGORY_NAMES_AR = {
  hanafi: "الحنفية",
  maliki: "المالكية",
  shafii: "الشافعية",
  hanbali: "الحنابلة",
};

const MAX_RESULTS_PER_QUERY = 20;
const MAX_FINAL_RESULTS = 10;

/* =========================================================
   QUERY MAP
========================================================= */

const QUERY_MAP = {
  puasa: "الصيام",
  "puasa ramadan": "صيام رمضان",
  zakat: "الزكاة",
  "zakat fitrah": "زكاة الفطر",

  solat: "الصلاة",
  sembahyang: "الصلاة",

  wuduk: "الوضوء",
  wudhu: "الوضوء",

  taharah: "الطهارة",
  bersuci: "الطهارة",

  tayamum: "التيمم",

  "mandi wajib": "الغسل",
  "mandi junub": "غسل الجنابة",
  "mandi selepas haid": "غسل الحيض",
  "mandi haid": "غسل الحيض",
  "mandi nifas": "غسل النفاس",

  junub: "الجنابة",
  haid: "الحيض",
  nifas: "النفاس",
  istihadah: "الاستحاضة",

  qunut: "القنوت",
  "qunut subuh": "القنوت في صلاة الصبح",

  "solat subuh": "صلاة الصبح",
  "solat jumaat": "صلاة الجمعة",
  jumaat: "صلاة الجمعة",

  azan: "الأذان",
  iqamah: "الإقامة",

  nikah: "النكاح",
  perkahwinan: "النكاح",

  talak: "الطلاق",
  cerai: "الطلاق",

  faraid: "الفرائض",
  pusaka: "المواريث",

  haji: "الحج",
  umrah: "العمرة",

  korban: "الأضحية",
  akikah: "العقيقة",

  sembelihan: "الذبائح",
  najis: "النجاسة",
  aurat: "العورة",

  mahar: "المهر",
  "mas kahwin": "المهر",

  "jual beli": "البيع",
  riba: "الربا",

  hutang: "الدين",
  pinjaman: "القرض",

  wakaf: "الوقف",
  nazar: "النذر",
  sumpah: "اليمين",
  kaffarah: "الكفارة",
  kafarah: "الكفارة",
};

/* =========================================================
   HELPERS
========================================================= */

function firstValue(obj, keys = []) {
  if (!obj || typeof obj !== "object") return null;

  for (const key of keys) {
    const value = obj[key];

    if (
      value !== undefined &&
      value !== null &&
      String(value).trim() !== ""
    ) {
      return value;
    }
  }

  return null;
}

function cleanText(value) {
  if (value === undefined || value === null) return "";

  if (typeof value === "string") {
    return value.replace(/\s+/g, " ").trim();
  }

  return String(value).replace(/\s+/g, " ").trim();
}

function findTextDeep(obj, depth = 0) {
  if (depth > 7 || obj === null || obj === undefined) {
    return "";
  }

  if (typeof obj === "string") {
    const text = cleanText(obj);

    if (text.length >= 30) {
      return text;
    }

    return "";
  }

  if (Array.isArray(obj)) {
    for (const item of obj) {
      const found = findTextDeep(item, depth + 1);

      if (found) return found;
    }

    return "";
  }

  if (typeof obj === "object") {
    const preferredKeys = [
      "text",
      "content",
      "snippet",
      "snip",
      "passage",
      "body",
      "quote",
      "raw_text",
      "rawText",
    ];

    for (const key of preferredKeys) {
      if (obj[key]) {
        const found = findTextDeep(obj[key], depth + 1);

        if (found) return found;
      }
    }

    for (const [key, value] of Object.entries(obj)) {
      if (
        [
          "title",
          "book",
          "book_name",
          "bookName",
          "author",
          "author_name",
          "authorName",
          "url",
          "link",
          "href",
        ].includes(key)
      ) {
        continue;
      }

      const found = findTextDeep(value, depth + 1);

      if (found) return found;
    }
  }

  return "";
}

function extractPage(obj) {
  const value = firstValue(obj, [
    "page",
    "page_number",
    "pageNumber",
    "page_no",
    "pageNo",
  ]);

  if (value !== null) return value;

  if (obj?.meta) {
    return firstValue(obj.meta, [
      "page",
      "page_number",
      "pageNumber",
      "page_no",
      "pageNo",
    ]);
  }

  return null;
}

function extractBookId(obj) {
  const value = firstValue(obj, [
    "book_id",
    "bookId",
    "bookid",
    "id_book",
  ]);

  if (value !== null) return value;

  if (obj?.meta) {
    return firstValue(obj.meta, [
      "book_id",
      "bookId",
      "bookid",
    ]);
  }

  return null;
}

function extractBook(obj) {
  const value = firstValue(obj, [
    "book",
    "book_name",
    "bookName",
    "title",
    "name",
  ]);

  if (typeof value === "object") {
    return firstValue(value, [
      "name",
      "title",
      "book_name",
      "bookName",
    ]);
  }

  return value;
}

function extractAuthor(obj) {
  const value = firstValue(obj, [
    "author",
    "author_name",
    "authorName",
  ]);

  if (typeof value === "object") {
    return firstValue(value, [
      "name",
      "author_name",
      "authorName",
    ]);
  }

  return value;
}

/*
 * URL rasmi kitab berdasarkan book_id.
 *
 * Kita hanya bina URL apabila book_id benar-benar wujud.
 * Tidak pernah cipta URL berdasarkan nama kitab.
 */
function buildBookUrl(bookId) {
  if (!bookId) return "";

  return `https://app.turath.io/book/${encodeURIComponent(bookId)}`;
}

function extractRawItems(raw) {
  if (!raw) return [];

  if (Array.isArray(raw)) return raw;

  if (Array.isArray(raw.results)) return raw.results;
  if (Array.isArray(raw.data)) return raw.data;
  if (Array.isArray(raw.items)) return raw.items;
  if (Array.isArray(raw.hits)) return raw.hits;
  if (Array.isArray(raw.sources)) return raw.sources;

  if (raw.data && typeof raw.data === "object") {
    if (Array.isArray(raw.data.results)) return raw.data.results;
    if (Array.isArray(raw.data.items)) return raw.data.items;
    if (Array.isArray(raw.data.hits)) return raw.data.hits;
  }

  return [];
}

/* =========================================================
   BOOK INFO
========================================================= */

async function enrichBook(item) {
  const bookId = extractBookId(item);

  let book = extractBook(item);
  let author = extractAuthor(item);
  let page = extractPage(item);
  let url = "";

  if (bookId) {
    url = buildBookUrl(bookId);

    try {
      const info = await getBookInfo(bookId);

      if (info) {
        book =
          book ||
          firstValue(info, [
            "book",
            "book_name",
            "bookName",
            "title",
            "name",
          ]) ||
          firstValue(info?.data, [
            "book",
            "book_name",
            "bookName",
            "title",
            "name",
          ]);

        author =
          author ||
          firstValue(info, [
            "author",
            "author_name",
            "authorName",
          ]) ||
          firstValue(info?.data, [
            "author",
            "author_name",
            "authorName",
          ]);

        page =
          page ||
          extractPage(info) ||
          extractPage(info?.data);

        const sdkUrl = firstValue(info, [
          "url",
          "link",
          "href",
          "book_url",
          "bookUrl",
          "source_url",
          "sourceUrl",
        ]);

        if (sdkUrl) {
          url = sdkUrl;
        }
      }
    } catch (error) {
      console.log(
        `⚠️ BOOK INFO ERROR ${bookId}: ${error.message}`
      );
    }
  }

  return {
    text: findTextDeep(item),

    book: cleanText(book),
    author: cleanText(author),

    page:
      page !== null && page !== undefined
        ? page
        : "",

    book_id: bookId || "",

    url: cleanText(url),

    category:
      item?.category ||
      item?.category_name ||
      item?.cat_name ||
      CATEGORY_NAMES_AR.shafii,

    raw: item,
  };
}

/* =========================================================
   NORMALIZE
========================================================= */

async function normalizeResults(rawItems) {
  const output = [];

  for (const item of rawItems) {
    const result = await enrichBook(item);

    if (!result.text) {
      continue;
    }

    output.push(result);
  }

  return output;
}

/* =========================================================
   SEARCH SDK
========================================================= */

async function sdkSearch(query, options = {}) {
  const limit =
    options.limit || MAX_RESULTS_PER_QUERY;

  /*
   * Cuba bentuk yang biasa digunakan oleh turath-sdk.
   */

  try {
    const result = await search({
      query,
      limit,
      categoryId: options.categoryId,
    });

    if (result) {
      return result;
    }
  } catch (error) {
    console.log(
      `⚠️ SEARCH OBJECT FORMAT: ${error.message}`
    );
  }

  try {
    const result = await search(query, {
      limit,
      categoryId: options.categoryId,
    });

    if (result) {
      return result;
    }
  } catch (error) {
    console.log(
      `⚠️ SEARCH STRING FORMAT: ${error.message}`
    );
  }

  try {
    const result = await search(query);

    if (result) {
      return result;
    }
  } catch (error) {
    console.log(
      `❌ SEARCH ERROR: ${error.message}`
    );
  }

  return {
    results: [],
    data: [],
  };
}

/* =========================================================
   SEARCH ONE QUERY
========================================================= */

async function searchOneQuery(query, options = {}) {
  console.log(`\n🔎 TURATH QUERY: ${query}`);

  const raw = await sdkSearch(query, options);

  const items = extractRawItems(raw);

  console.log(`📚 RAW ITEMS: ${items.length}`);

  const normalized = await normalizeResults(items);

  console.log(
    `📚 NORMALIZED: ${normalized.length}`
  );

  return normalized;
}

/* =========================================================
   DEDUPLICATE
========================================================= */

function deduplicate(results) {
  const seen = new Set();
  const output = [];

  for (const item of results) {
    const key = [
      item.book_id || item.book,
      item.page,
      item.text.slice(0, 180),
    ]
      .join("|")
      .toLowerCase();

    if (seen.has(key)) continue;

    seen.add(key);
    output.push(item);
  }

  return output;
}

/* =========================================================
   SIMPLE RELEVANCE SCORE
========================================================= */

function scoreResult(result, query) {
  let score = 0;

  const text =
    `${result.text} ${result.book} ${result.author}`
      .toLowerCase();

  const terms = query
    .toLowerCase()
    .split(/\s+/)
    .filter((x) => x.length >= 3);

  for (const term of terms) {
    if (text.includes(term)) {
      score += 2;
    }
  }

  if (result.text.length > 100) {
    score += 1;
  }

  if (result.book) {
    score += 1;
  }

  if (result.author) {
    score += 1;
  }

  if (result.page !== "") {
    score += 1;
  }

  if (result.url) {
    score += 1;
  }

  return score;
}

/* =========================================================
   MULTI QUERY
========================================================= */

async function multiSearch(queries, options = {}) {
  const all = [];

  const cleanQueries = [
    ...new Set(
      queries
        .map((q) => cleanText(q))
        .filter(Boolean)
    ),
  ].slice(0, 8);

  console.log(
    `\n🧠 GEMINI GENERATED QUERIES: ${cleanQueries.length}`
  );

  for (const query of cleanQueries) {
    const results = await searchOneQuery(
      query,
      options
    );

    for (const result of results) {
      all.push({
        ...result,
        query,
        _score: scoreResult(result, query),
      });
    }
  }

  const unique = deduplicate(all);

  unique.sort(
    (a, b) => b._score - a._score
  );

  const finalResults = unique
    .slice(0, MAX_FINAL_RESULTS)
    .map((item) => {
      const clean = { ...item };
      delete clean._score;
      delete clean.query;
      delete clean.raw;
      return clean;
    });

  return finalResults;
}

/* =========================================================
   HTTP
========================================================= */

function sendJson(res, statusCode, data) {
  const body = JSON.stringify(
    data,
    null,
    2
  );

  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });

  res.end(body);
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";

    req.on("data", (chunk) => {
      body += chunk;
    });

    req.on("end", () => {
      if (!body) {
        resolve({});
        return;
      }

      try {
        resolve(JSON.parse(body));
      } catch {
        resolve({});
      }
    });

    req.on("error", reject);
  });
}

/* =========================================================
   SERVER
========================================================= */

const server = http.createServer(
  async (req, res) => {
    try {
      const url = new URL(
        req.url,
        `http://${HOST}:${PORT}`
      );

      /* HEALTH */

      if (
        req.method === "GET" &&
        url.pathname === "/health"
      ) {
        return sendJson(res, 200, {
          ok: true,
          service: "turath",
          status: "running",
          target_sources: MAX_FINAL_RESULTS,
        });
      }

      /* CATEGORIES */

      if (
        req.method === "GET" &&
        url.pathname === "/categories"
      ) {
        return sendJson(res, 200, {
          ok: true,
          categories: CATEGORY_IDS,
        });
      }

      /* SEARCH GET */

      if (
        req.method === "GET" &&
        url.pathname === "/search"
      ) {
        const query =
          url.searchParams.get("q") ||
          url.searchParams.get("query") ||
          "";

        const queriesParam =
          url.searchParams.get("queries");

        let queries = [];

        if (queriesParam) {
          try {
            queries = JSON.parse(queriesParam);
          } catch {
            queries = [];
          }
        }

        if (!queries.length && query) {
          queries = [query];
        }

        if (!queries.length) {
          return sendJson(res, 400, {
            ok: false,
            error: "query diperlukan",
          });
        }

        const category =
          url.searchParams.get("category") ||
          "shafii";

        const categoryId =
          CATEGORY_IDS[category] ||
          CATEGORY_IDS.shafii;

        const results = await multiSearch(
          queries,
          {
            categoryId,
          }
        );

        console.log(
          `\n📚 TURATH FINAL: ${results.length}`
        );

        results.forEach((r, i) => {
          console.log(
            `\n[${i + 1}] ${r.book}`
          );

          console.log(
            `✍️ ${r.author}`
          );

          console.log(
            `📄 ${r.page}`
          );

          console.log(
            `🆔 ${r.book_id}`
          );

          console.log(
            `🔗 ${r.url || "TIADA"}`
          );

          console.log(
            `📝 ${r.text.slice(0, 250)}`
          );
        });

        return sendJson(res, 200, {
          ok: true,
          query,
          queries,
          category,
          count: results.length,
          results,
          sources: results,
          data: results,
        });
      }

      /* SEARCH POST */

      if (
        req.method === "POST" &&
        url.pathname === "/search"
      ) {
        const body = await parseBody(req);

        let queries = [];

        if (Array.isArray(body.queries)) {
          queries = body.queries;
        } else if (body.query) {
          queries = [body.query];
        }

        if (!queries.length) {
          return sendJson(res, 400, {
            ok: false,
            error: "query atau queries diperlukan",
          });
        }

        const category =
          body.category || "shafii";

        const categoryId =
          CATEGORY_IDS[category] ||
          CATEGORY_IDS.shafii;

        const results = await multiSearch(
          queries,
          {
            categoryId,
          }
        );

        return sendJson(res, 200, {
          ok: true,
          query: body.query || "",
          queries,
          category,
          count: results.length,
          results,
          sources: results,
          data: results,
        });
      }

      /* ROOT */

      if (
        req.method === "GET" &&
        url.pathname === "/"
      ) {
        return sendJson(res, 200, {
          ok: true,
          service: "TanyaFiqihBot Turath Service",
          version: "2.0",
          target_sources: MAX_FINAL_RESULTS,
          categories: CATEGORY_IDS,
        });
      }

      return sendJson(res, 404, {
        ok: false,
        error: "Not found",
      });
    } catch (error) {
      console.error(
        "❌ TURATH SERVER ERROR:",
        error
      );

      return sendJson(res, 500, {
        ok: false,
        error: error.message,
      });
    }
  }
);

server.listen(PORT, HOST, () => {
  console.log("==============================================");
  console.log("🚀 TURATH SERVICE STARTED");
  console.log(`📡 http://${HOST}:${PORT}`);
  console.log(
    `📚 CATEGORY IDS:`,
    CATEGORY_IDS
  );
  console.log(
    `🎯 TARGET SOURCES: ${MAX_FINAL_RESULTS}`
  );
  console.log("==============================================");
});
