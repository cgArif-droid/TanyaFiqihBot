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

const QUERY_MAP = {
  "puasa": "الصيام",
  "puasa ramadan": "صيام رمضان",
  "zakat": "الزكاة",
  "zakat fitrah": "زكاة الفطر",

  "solat": "الصلاة",
  "sembahyang": "الصلاة",
  "wuduk": "الوضوء",
  "wudhu": "الوضوء",
  "taharah": "الطهارة",
  "bersuci": "الطهارة",
  "tayamum": "التيمم",

  "mandi wajib": "الغسل",
  "mandi junub": "غسل الجنابة",
  "mandi selepas haid": "غسل الحيض",
  "mandi haid": "غسل الحيض",
  "mandi nifas": "غسل النفاس",
  "junub": "الجنابة",

  "haid": "الحيض",
  "nifas": "النفاس",
  "istihadah": "الاستحاضة",

  "qunut": "القنوت",
  "qunut subuh": "القنوت في صلاة الصبح",
  "solat subuh": "صلاة الصبح",
  "solat jumaat": "صلاة الجمعة",
  "jumaat": "صلاة الجمعة",
  "azan": "الأذان",
  "iqamah": "الإقامة",

  "nikah": "النكاح",
  "perkahwinan": "النكاح",
  "talak": "الطلاق",
  "cerai": "الطلاق",

  "faraid": "الفرائض",
  "pusaka": "المواريث",

  "haji": "الحج",
  "umrah": "العمرة",
  "korban": "الأضحية",
  "akikah": "العقيقة",
  "sembelihan": "الذبائح",

  "najis": "النجاسة",
  "aurat": "العورة",
  "mahar": "المهر",
  "mas kahwin": "المهر",

  "jual beli": "البيع",
  "riba": "الربا",
  "hutang": "الدين",
  "pinjaman": "القرض",

  "wakaf": "الوقف",
  "nazar": "النذر",
  "sumpah": "اليمين",
  "kaffarah": "الكفارة",
  "kafarah": "الكفارة",
};

function normalizeText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();

  if (Array.isArray(value)) {
    return value
      .map(normalizeText)
      .filter(Boolean)
      .join("\n")
      .trim();
  }

  if (typeof value === "object") {
    const candidates = [
      value.text,
      value.content,
      value.body,
      value.description,
      value.snippet,
      value.html,
      value.value,
    ];

    for (const item of candidates) {
      const result = normalizeText(item);
      if (result) return result;
    }
  }

  return "";
}

function firstValue(obj, keys) {
  if (!obj || typeof obj !== "object") return null;

  for (const key of keys) {
    if (
      Object.prototype.hasOwnProperty.call(obj, key) &&
      obj[key] !== null &&
      obj[key] !== undefined &&
      obj[key] !== ""
    ) {
      return obj[key];
    }
  }

  return null;
}

function numberValue(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const n = Number(value);

  return Number.isFinite(n) ? n : null;
}

function findTextDeep(value, depth = 0) {
  if (depth > 6 || value === null || value === undefined) {
    return "";
  }

  if (typeof value === "string") {
    const text = value.trim();

    if (text.length >= 20) {
      return text;
    }

    return "";
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      const result = findTextDeep(item, depth + 1);

      if (result) return result;
    }

    return "";
  }

  if (typeof value === "object") {
    const preferred = [
      "text",
      "content",
      "body",
      "snippet",
      "description",
      "passage",
      "quote",
      "html",
    ];

    for (const key of preferred) {
      if (value[key] !== undefined) {
        const result = findTextDeep(value[key], depth + 1);

        if (result) return result;
      }
    }

    for (const [key, child] of Object.entries(value)) {
      if (
        [
          "title",
          "book",
          "bookTitle",
          "author",
          "page",
          "book_id",
          "bookId",
          "category",
          "id",
          "url",
        ].includes(key)
      ) {
        continue;
      }

      const result = findTextDeep(child, depth + 1);

      if (result) return result;
    }
  }

  return "";
}

function extractBookId(obj) {
  return numberValue(
    firstValue(obj, [
      "book_id",
      "bookId",
      "bookID",
      "bookid",
      "book",
      "volume_id",
      "volumeId",
    ])
  );
}

function extractPage(obj) {
  return numberValue(
    firstValue(obj, [
      "page",
      "page_number",
      "pageNumber",
      "pageno",
      "pageNo",
    ])
  );
}

function extractBook(obj) {
  const value = firstValue(obj, [
    "book_title",
    "bookTitle",
    "book_name",
    "bookName",
    "title_book",
  ]);

  if (value) return normalizeText(value);

  const book = obj?.book;

  if (typeof book === "string") {
    return book.trim();
  }

  if (book && typeof book === "object") {
    return (
      normalizeText(
        firstValue(book, [
          "title",
          "name",
          "book_title",
          "bookTitle",
        ])
      ) || ""
    );
  }

  return "";
}

function extractAuthor(obj) {
  const value = firstValue(obj, [
    "author",
    "author_name",
    "authorName",
    "writer",
  ]);

  if (typeof value === "string") {
    return value.trim();
  }

  if (value && typeof value === "object") {
    return (
      normalizeText(
        firstValue(value, [
          "name",
          "title",
          "author",
        ])
      ) || ""
    );
  }

  return "";
}

function extractUrl(obj) {
  const value = firstValue(obj, [
    "url",
    "link",
    "href",
    "source_url",
    "sourceUrl",
  ]);

  return typeof value === "string" ? value.trim() : "";
}

function collectPossibleResults(raw) {
  if (!raw) return [];

  if (Array.isArray(raw)) {
    return raw;
  }

  if (typeof raw !== "object") {
    return [];
  }

  const keys = [
    "results",
    "data",
    "items",
    "hits",
    "documents",
    "records",
    "books",
    "pages",
  ];

  for (const key of keys) {
    if (Array.isArray(raw[key])) {
      return raw[key];
    }
  }

  if (raw.data && typeof raw.data === "object") {
    return collectPossibleResults(raw.data);
  }

  return [raw];
}

function extractResult(raw, categoryKey, fallbackBookInfo = null) {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  const source = raw;

  let book = extractBook(source);
  let author = extractAuthor(source);
  let page = extractPage(source);
  let bookId = extractBookId(source);
  let url = extractUrl(source);

  if (!book && fallbackBookInfo) {
    book =
      normalizeText(
        firstValue(fallbackBookInfo, [
          "title",
          "name",
          "book_title",
          "bookTitle",
        ])
      ) || "";
  }

  if (!author && fallbackBookInfo) {
    author =
      normalizeText(
        firstValue(fallbackBookInfo, [
          "author",
          "author_name",
          "authorName",
        ])
      ) || "";
  }

  if (!bookId && fallbackBookInfo) {
    bookId = numberValue(
      firstValue(fallbackBookInfo, [
        "id",
        "book_id",
        "bookId",
      ])
    );
  }

  let text = findTextDeep(source);

  if (!text) {
    text = normalizeText(
      firstValue(source, [
        "text",
        "content",
        "body",
        "snippet",
        "passage",
      ])
    );
  }

  if (!text) {
    return null;
  }

  return {
    text,
    book: book || "Kitab tidak dikenal",
    author: author || "Pengarang tidak diketahui",
    page,
    book_id: bookId,
    url,
    category: CATEGORY_NAMES_AR[categoryKey] || categoryKey,
  };
}

async function enrichResult(result) {
  if (!result) return null;

  if (!result.book_id) {
    return result;
  }

  try {
    const info = await getBookInfo(result.book_id);

    if (info) {
      if (
        !result.book ||
        result.book === "Kitab tidak dikenal"
      ) {
        result.book =
          normalizeText(
            firstValue(info, [
              "title",
              "name",
              "book_title",
              "bookTitle",
            ])
          ) || result.book;
      }

      if (
        !result.author ||
        result.author === "Pengarang tidak diketahui"
      ) {
        result.author =
          normalizeText(
            firstValue(info, [
              "author",
              "author_name",
              "authorName",
            ])
          ) || result.author;
      }

      if (!result.url) {
        result.url =
          normalizeText(
            firstValue(info, [
              "url",
              "link",
              "href",
            ])
          ) || "";
      }
    }
  } catch (error) {
    console.log(
      `⚠️ Gagal getBookInfo(${result.book_id}):`,
      error.message
    );
  }

  return result;
}

function applyQueryMap(query) {
  const original = String(query || "").trim();

  if (!original) return "";

  const lower = original.toLowerCase();

  if (QUERY_MAP[lower]) {
    return QUERY_MAP[lower];
  }

  let result = original;

  const sorted = Object.keys(QUERY_MAP).sort(
    (a, b) => b.length - a.length
  );

  for (const key of sorted) {
    if (lower.includes(key)) {
      result = `${QUERY_MAP[key]} ${result}`;
      break;
    }
  }

  return result.trim();
}

function isComparison(query) {
  const q = String(query || "").toLowerCase();

  const names = [
    "hanafi",
    "maliki",
    "syafie",
    "syafi'i",
    "syafii",
    "hanbali",
  ];

  const found = names.filter((name) =>
    q.includes(name)
  );

  return found.length >= 2;
}

async function performSearch(query, categoryKey) {
  const categoryId = CATEGORY_IDS[categoryKey];

  const arabicQuery = applyQueryMap(query);

  console.log(
    `🔎 SEARCH [${categoryKey}] ${arabicQuery}`
  );

  const raw = await search(arabicQuery, {
    categoryId,
  });

  console.log(
    `📦 RAW TYPE: ${Array.isArray(raw) ? "array" : typeof raw}`
  );

  console.log(
    "🧪 FIRST RAW TURATH RESULT:"
  );

  try {
    console.log(
      JSON.stringify(
        Array.isArray(raw) ? raw[0] : raw,
        null,
        2
      ).slice(0, 12000)
    );
  } catch {
    console.log(raw);
  }

  const possible = collectPossibleResults(raw);

  console.log(
    `📊 POSSIBLE RESULTS: ${possible.length}`
  );

  const results = [];

  for (const item of possible) {
    const normalized = extractResult(
      item,
      categoryKey
    );

    if (!normalized) {
      continue;
    }

    const enriched = await enrichResult(normalized);

    if (enriched) {
      results.push(enriched);
    }

    if (results.length >= 10) {
      break;
    }
  }

  return results;
}

async function searchAll(query) {
  const all = [];

  for (const categoryKey of [
    "shafii",
    "hanafi",
    "maliki",
    "hanbali",
  ]) {
    try {
      const results = await performSearch(
        query,
        categoryKey
      );

      all.push(...results);
    } catch (error) {
      console.log(
        `⚠️ SEARCH ERROR [${categoryKey}]:`,
        error.message
      );
    }
  }

  return all.slice(0, 20);
}

async function searchShafii(query) {
  return await performSearch(query, "shafii");
}

function sendJson(res, status, data) {
  const body = JSON.stringify(data);

  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "no-store",
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
      } catch (error) {
        reject(error);
      }
    });

    req.on("error", reject);
  });
}

const server = http.createServer(
  async (req, res) => {
    try {
      const url = new URL(
        req.url,
        `http://${HOST}:${PORT}`
      );

      if (
        req.method === "OPTIONS"
      ) {
        sendJson(res, 200, { ok: true });
        return;
      }

      if (
        req.method === "GET" &&
        url.pathname === "/"
      ) {
        sendJson(res, 200, {
          ok: true,
          service: "TanyaFiqihBot Turath Service",
          status: "running",
          categories: CATEGORY_IDS,
        });
        return;
      }

      if (
        req.method === "GET" &&
        url.pathname === "/health"
      ) {
        sendJson(res, 200, {
          ok: true,
          status: "healthy",
        });
        return;
      }

      if (
        req.method === "GET" &&
        url.pathname === "/categories"
      ) {
        sendJson(res, 200, {
          ok: true,
          categories: CATEGORY_IDS,
          names: CATEGORY_NAMES_AR,
        });
        return;
      }

      if (
        req.method === "GET" &&
        url.pathname === "/search"
      ) {
        const query =
          url.searchParams.get("q") ||
          url.searchParams.get("query") ||
          "";

        const comparison =
          url.searchParams.get("comparison") === "true" ||
          isComparison(query);

        if (!query.trim()) {
          sendJson(res, 400, {
            ok: false,
            error: "Query kosong",
            results: [],
          });
          return;
        }

        console.log(
          `\n🔍 TURATH SEARCH: ${query}`
        );

        const results = comparison
          ? await searchAll(query)
          : await searchShafii(query);

        console.log(
          `\n📚 TURATH RESULTS: ${results.length}`
        );

        for (const item of results) {
          console.log(
            `📖 KITAB: ${item.book}`
          );

          console.log(
            `✍️ PENGARANG: ${item.author}`
          );

          console.log(
            `📄 HALAMAN: ${item.page ?? "TIADA"}`
          );

          console.log(
            `🆔 BOOK ID: ${item.book_id ?? "TIADA"}`
          );

          console.log(
            `🔗 URL: ${item.url || "TIADA"}`
          );

          console.log(
            `⚖️ KATEGORI: ${item.category}`
          );

          console.log("----------------------------------------------");
        }

        sendJson(res, 200, {
          ok: true,
          query,
          arabic_query: applyQueryMap(query),
          comparison,
          count: results.length,

          // Format utama untuk app.py
          results,

          // Alias tambahan untuk compatibility
          sources: results,
          data: results,
        });

        return;
      }

      if (
        req.method === "POST" &&
        url.pathname === "/search"
      ) {
        const body = await parseBody(req);

        const query =
          body.query ||
          body.q ||
          "";

        const comparison =
          body.comparison === true ||
          isComparison(query);

        if (!query.trim()) {
          sendJson(res, 400, {
            ok: false,
            error: "Query kosong",
            results: [],
          });
          return;
        }

        const results = comparison
          ? await searchAll(query)
          : await searchShafii(query);

        sendJson(res, 200, {
          ok: true,
          query,
          arabic_query: applyQueryMap(query),
          comparison,
          count: results.length,
          results,
          sources: results,
          data: results,
        });

        return;
      }

      const pageMatch =
        url.pathname.match(
          /^\/page\/(\d+)\/(\d+)$/
        );

      if (
        req.method === "GET" &&
        pageMatch
      ) {
        const bookId = Number(pageMatch[1]);
        const page = Number(pageMatch[2]);

        const result = await getPage(
          bookId,
          page
        );

        sendJson(res, 200, {
          ok: true,
          book_id: bookId,
          page,
          result,
        });

        return;
      }

      const bookMatch =
        url.pathname.match(
          /^\/book\/(\d+)$/
        );

      if (
        req.method === "GET" &&
        bookMatch
      ) {
        const bookId = Number(bookMatch[1]);

        const result = await getBookInfo(
          bookId
        );

        sendJson(res, 200, {
          ok: true,
          book_id: bookId,
          result,
        });

        return;
      }

      sendJson(res, 404, {
        ok: false,
        error: "Route tidak ditemui",
      });
    } catch (error) {
      console.error(
        "❌ TURATH SERVICE ERROR:",
        error
      );

      sendJson(res, 500, {
        ok: false,
        error: error.message,
        results: [],
      });
    }
  }
);

server.listen(
  PORT,
  HOST,
  () => {
    console.log("");
    console.log("==============================================");
    console.log("🚀 TURATH SERVICE STARTED");
    console.log(
      `📡 http://${HOST}:${PORT}`
    );
    console.log(
      "📚 CATEGORY IDS:",
      CATEGORY_IDS
    );
    console.log("==============================================");
    console.log("");
  }
);
