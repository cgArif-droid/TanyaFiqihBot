import http from "http";
import { search, getBookInfo } from "turath-sdk";

const PORT = Number(process.env.TURATH_PORT || 8765);
const HOST = process.env.TURATH_HOST || "127.0.0.1";

const MAX_RESULTS_PER_QUERY = 20;
const MAX_FINAL_RESULTS = 10;

/* =========================================================
   CATEGORY
========================================================= */

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

/* =========================================================
   QUERY MAP
========================================================= */

const QUERY_MAP = {
  puasa: "الصيام",
  "puasa ramadan": "صيام رمضان",
  "hukum puasa": "أحكام الصيام",
  "batal puasa": "مفسدات الصيام",
  "membatalkan puasa": "مفسدات الصيام",

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

function cleanText(value) {
  if (value === undefined || value === null) {
    return "";
  }

  if (typeof value === "string") {
    return value.replace(/\s+/g, " ").trim();
  }

  return String(value)
    .replace(/\s+/g, " ")
    .trim();
}


function firstValue(obj, keys = []) {
  if (!obj || typeof obj !== "object") {
    return null;
  }

  for (const key of keys) {
    if (
      obj[key] !== undefined &&
      obj[key] !== null &&
      String(obj[key]).trim() !== ""
    ) {
      return obj[key];
    }
  }

  return null;
}


/* =========================================================
   RECURSIVE OBJECT SEARCH
========================================================= */

function findValueDeep(
  obj,
  keys = [],
  depth = 0,
  visited = new Set()
) {
  if (
    depth > 10 ||
    obj === null ||
    obj === undefined
  ) {
    return "";
  }

  if (
    typeof obj !== "object"
  ) {
    return "";
  }

  if (visited.has(obj)) {
    return "";
  }

  visited.add(obj);

  // Direct keys dahulu
  for (const key of keys) {
    if (
      Object.prototype.hasOwnProperty.call(obj, key) &&
      obj[key] !== undefined &&
      obj[key] !== null
    ) {
      const value = obj[key];

      if (
        typeof value === "string" &&
        value.trim()
      ) {
        return cleanText(value);
      }

      if (
        typeof value === "number"
      ) {
        return value;
      }

      if (
        typeof value === "object"
      ) {
        const nested =
          findValueDeep(
            value,
            keys,
            depth + 1,
            visited
          );

        if (nested) {
          return nested;
        }
      }
    }
  }

  // Kemudian nested objects
  for (const [key, value] of Object.entries(obj)) {

    if (
      value === null ||
      value === undefined
    ) {
      continue;
    }

    if (
      typeof value === "object"
    ) {
      const result =
        findValueDeep(
          value,
          keys,
          depth + 1,
          visited
        );

      if (result) {
        return result;
      }
    }
  }

  return "";
}


/* =========================================================
   DEEP TEXT
========================================================= */

function findTextDeep(
  obj,
  depth = 0
) {
  if (
    depth > 10 ||
    obj === null ||
    obj === undefined
  ) {
    return "";
  }

  if (
    typeof obj === "string"
  ) {
    const text =
      cleanText(obj);

    if (
      text.length >= 30
    ) {
      return text;
    }

    return "";
  }

  if (
    Array.isArray(obj)
  ) {
    for (
      const item of obj
    ) {
      const result =
        findTextDeep(
          item,
          depth + 1
        );

      if (result) {
        return result;
      }
    }

    return "";
  }

  if (
    typeof obj === "object"
  ) {

    const preferredKeys = [
      "text",
      "content",
      "snippet",
      "passage",
      "body",
      "quote",
      "raw_text",
      "rawText",
      "text_content",
      "textContent",
    ];

    for (
      const key of preferredKeys
    ) {

      if (
        obj[key] !== undefined &&
        obj[key] !== null
      ) {

        const result =
          findTextDeep(
            obj[key],
            depth + 1
          );

        if (result) {
          return result;
        }
      }
    }

    for (
      const [key, value]
      of Object.entries(obj)
    ) {

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

      const result =
        findTextDeep(
          value,
          depth + 1
        );

      if (result) {
        return result;
      }
    }
  }

  return "";
}


/* =========================================================
   PAGE
========================================================= */

function extractPage(obj) {

  const direct =
    findValueDeep(
      obj,
      [
        "page",
        "page_number",
        "pageNumber",
        "page_no",
        "pageNo",
        "pageno",
        "pageNum",
      ]
    );

  if (
    direct !== ""
  ) {
    return direct;
  }

  return "";
}


/* =========================================================
   BOOK ID
========================================================= */

function extractBookId(obj) {

  const direct =
    findValueDeep(
      obj,
      [
        "book_id",
        "bookId",
        "bookid",
        "id_book",
        "bookID",
      ]
    );

  if (
    direct !== ""
  ) {
    return direct;
  }

  /*
   * Kalau object itu sendiri ada id
   * dan nampak seperti metadata kitab.
   */

  if (
    obj &&
    typeof obj === "object"
  ) {

    const id =
      firstValue(
        obj,
        ["id"]
      );

    if (
      id !== null &&
      id !== undefined
    ) {

      const numeric =
        String(id).trim();

      if (
        /^\d+$/.test(numeric)
      ) {
        return numeric;
      }
    }
  }

  return "";
}


/* =========================================================
   BOOK NAME
========================================================= */

function extractBook(obj) {

  if (
    !obj ||
    typeof obj !== "object"
  ) {
    return "";
  }

  /*
   * Keutamaan field nama kitab.
   */

  const value =
    findValueDeep(
      obj,
      [
        "book_name",
        "bookName",
        "book_title",
        "bookTitle",
        "kitab_name",
        "kitabName",
        "name_ar",
        "nameAr",
        "title_ar",
        "titleAr",
      ]
    );

  if (
    value
  ) {
    return cleanText(value);
  }

  /*
   * Cari object book.
   */

  if (
    obj.book &&
    typeof obj.book === "object"
  ) {

    const nested =
      findValueDeep(
        obj.book,
        [
          "name",
          "title",
          "book_name",
          "bookName",
          "name_ar",
          "nameAr",
          "title_ar",
          "titleAr",
        ]
      );

    if (
      nested
    ) {
      return cleanText(nested);
    }
  }

  /*
   * Last fallback:
   * field book jika string.
   */

  if (
    typeof obj.book === "string"
  ) {
    return cleanText(
      obj.book
    );
  }

  return "";
}


/* =========================================================
   AUTHOR
========================================================= */

function extractAuthor(obj) {

  if (
    !obj ||
    typeof obj !== "object"
  ) {
    return "";
  }

  const value =
    findValueDeep(
      obj,
      [
        "author_name",
        "authorName",
        "author_full_name",
        "authorFullName",
        "writer_name",
        "writerName",
        "muallif",
        "muallif_name",
        "author_ar",
        "authorAr",
      ]
    );

  if (
    value
  ) {
    return cleanText(value);
  }

  /*
   * Nested author object
   */

  if (
    obj.author &&
    typeof obj.author === "object"
  ) {

    const nested =
      findValueDeep(
        obj.author,
        [
          "name",
          "full_name",
          "fullName",
          "author_name",
          "authorName",
        ]
      );

    if (
      nested
    ) {
      return cleanText(nested);
    }
  }

  if (
    typeof obj.author === "string"
  ) {
    return cleanText(
      obj.author
    );
  }

  return "";
}


/* =========================================================
   URL
========================================================= */

function extractUrl(obj) {

  const value =
    findValueDeep(
      obj,
      [
        "url",
        "link",
        "href",
        "book_url",
        "bookUrl",
        "source_url",
        "sourceUrl",
      ]
    );

  if (
    value
  ) {
    return cleanText(
      value
    );
  }

  return "";
}


function buildBookUrl(bookId) {

  if (
    bookId === undefined ||
    bookId === null ||
    String(bookId).trim() === ""
  ) {
    return "";
  }

  return (
    "https://app.turath.io/book/" +
    encodeURIComponent(
      String(bookId)
    )
  );
}


/* =========================================================
   DEBUG BOOK INFO
========================================================= */

function debugBookInfo(
  bookId,
  info
) {

  console.log(
    "\n========================================"
  );

  console.log(
    `🧪 RAW BOOK INFO FOR ID: ${bookId}`
  );

  console.log(
    "========================================"
  );

  try {

    console.log(
      JSON.stringify(
        info,
        null,
        2
      ).slice(
        0,
        12000
      )
    );

  } catch {

    console.log(
      info
    );
  }

  console.log(
    "========================================\n"
  );
}


/* =========================================================
   ENRICH BOOK
========================================================= */

async function enrichBook(
  item
) {

  let book =
    extractBook(item);

  let author =
    extractAuthor(item);

  let page =
    extractPage(item);

  let bookId =
    extractBookId(item);

  let url =
    extractUrl(item);

  /*
   * Debug item structure
   */

  console.log(
    "\n🧪 ITEM METADATA:"
  );

  console.log(
    {
      book,
      author,
      page,
      bookId,
      url,
    }
  );

  /*
   * Kalau ada book object
   */

  if (
    item?.book &&
    typeof item.book === "object"
  ) {

    if (!book) {
      book =
        extractBook(
          item.book
        );
    }

    if (!author) {
      author =
        extractAuthor(
          item.book
        );
    }

    if (!bookId) {
      bookId =
        extractBookId(
          item.book
        );
    }

    if (!url) {
      url =
        extractUrl(
          item.book
        );
    }
  }

  /*
   * GET BOOK INFO
   */

  if (
    bookId
  ) {

    try {

      console.log(
        `📖 GET BOOK INFO: ${bookId}`
      );

      const info =
        await getBookInfo(
          bookId
        );

      if (
        info
      ) {

        /*
         * PENTING:
         * Paparkan struktur sebenar
         * getBookInfo untuk diagnosis.
         */

        debugBookInfo(
          bookId,
          info
        );

        if (!book) {
          book =
            extractBook(
              info
            );
        }

        if (!author) {
          author =
            extractAuthor(
              info
            );
        }

        if (!page) {
          page =
            extractPage(
              info
            );
        }

        if (!url) {
          url =
            extractUrl(
              info
            );
        }

        /*
         * Ada kemungkinan metadata berada
         * dalam result.data
         */

        if (
          info.data
        ) {

          if (!book) {
            book =
              extractBook(
                info.data
              );
          }

          if (!author) {
            author =
              extractAuthor(
                info.data
              );
          }

          if (!page) {
            page =
              extractPage(
                info.data
              );
          }

          if (!url) {
            url =
              extractUrl(
                info.data
              );
          }
        }
      }

    } catch (
      error
    ) {

      console.log(
        `⚠️ GET BOOK INFO ERROR ${bookId}:`,
        error.message
      );
    }
  }

  /*
   * URL fallback
   */

  if (
    !url &&
    bookId
  ) {
    url =
      buildBookUrl(
        bookId
      );
  }

  /*
   * Text
   */

  const text =
    findTextDeep(
      item
    );

  return {

    text,

    book:
      cleanText(
        book
      ),

    author:
      cleanText(
        author
      ),

    page:
      page === null ||
      page === undefined
        ? ""
        : page,

    book_id:
      bookId || "",

    url:
      cleanText(
        url
      ),

    category:
      item?.category ||
      item?.category_name ||
      item?.categoryName ||
      "",

    raw:
      item,
  };
}


/* =========================================================
   EXTRACT SEARCH DATA
========================================================= */

function extractRawItems(
  raw
) {

  if (!raw) {
    return [];
  }

  if (
    Array.isArray(raw)
  ) {
    return raw;
  }

  if (
    Array.isArray(
      raw.data
    )
  ) {
    return raw.data;
  }

  if (
    Array.isArray(
      raw.results
    )
  ) {
    return raw.results;
  }

  if (
    Array.isArray(
      raw.items
    )
  ) {
    return raw.items;
  }

  if (
    Array.isArray(
      raw.hits
    )
  ) {
    return raw.hits;
  }

  if (
    Array.isArray(
      raw.sources
    )
  ) {
    return raw.sources;
  }

  if (
    raw.data &&
    typeof raw.data === "object"
  ) {

    if (
      Array.isArray(
        raw.data.data
      )
    ) {
      return raw.data.data;
    }

    if (
      Array.isArray(
        raw.data.results
      )
    ) {
      return raw.data.results;
    }

    if (
      Array.isArray(
        raw.data.items
      )
    ) {
      return raw.data.items;
    }
  }

  return [];
}


/* =========================================================
   SDK SEARCH
========================================================= */

async function sdkSearch(
  query,
  options = {}
) {

  const category =
    options.category ??
    CATEGORY_IDS.shafii;

  console.log(
    "\n🔧 SDK SEARCH"
  );

  console.log(
    "QUERY:",
    query
  );

  console.log(
    "CATEGORY:",
    category
  );

  try {

    const result =
      await search(
        query,
        {
          category,
          page: 1,
        }
      );

    console.log(
      "🧪 SEARCH RESULT TYPE:",
      typeof result
    );

    if (
      result &&
      typeof result === "object"
    ) {

      console.log(
        "🧪 SEARCH RESULT KEYS:",
        Object.keys(
          result
        )
      );

      console.log(
        "🧪 SEARCH COUNT:",
        result.count
      );

      console.log(
        "🧪 SEARCH DATA LENGTH:",
        Array.isArray(
          result.data
        )
          ? result.data.length
          : "NOT ARRAY"
      );

      console.log(
        "🧪 FIRST RAW TURATH RESULT:"
      );

      console.log(
        JSON.stringify(
          result,
          null,
          2
        ).slice(
          0,
          6000
        )
      );
    }

    return result;

  } catch (
    error
  ) {

    console.error(
      "❌ TURATH SEARCH ERROR:",
      error
    );

    return {
      count: 0,
      data: [],
    };
  }
}


/* =========================================================
   SEARCH ONE QUERY
========================================================= */

async function searchOneQuery(
  query,
  options = {}
) {

  console.log(
    "\n========================================"
  );

  console.log(
    `🔎 TURATH QUERY: ${query}`
  );

  const raw =
    await sdkSearch(
      query,
      options
    );

  const items =
    extractRawItems(
      raw
    );

  console.log(
    `📚 RAW ITEMS: ${items.length}`
  );

  const normalized = [];

  /*
   * Hadkan jumlah setiap query
   */

  const limitedItems =
    items.slice(
      0,
      MAX_RESULTS_PER_QUERY
    );

  for (
    const item
    of limitedItems
  ) {

    try {

      const result =
        await enrichBook(
          item
        );

      if (
        !result.text
      ) {

        console.log(
          "⚠️ RESULT TIADA TEXT"
        );

        continue;
      }

      normalized.push(
        result
      );

    } catch (
      error
    ) {

      console.log(
        "⚠️ NORMALIZE ERROR:",
        error.message
      );
    }
  }

  console.log(
    `📚 NORMALIZED: ${normalized.length}`
  );

  return normalized;
}


/* =========================================================
   DEDUPLICATE
========================================================= */

function deduplicate(
  results
) {

  const seen =
    new Set();

  const output = [];

  for (
    const item
    of results
  ) {

    const key =
      [
        item.book_id ||
          item.book,

        item.page,

        item.text
          .slice(
            0,
            250
          ),
      ]
        .join("|")
        .toLowerCase();

    if (
      seen.has(key)
    ) {
      continue;
    }

    seen.add(
      key
    );

    output.push(
      item
    );
  }

  return output;
}


/* =========================================================
   SCORE
========================================================= */

function scoreResult(
  result,
  query
) {

  let score = 0;

  const text =
    (
      result.text +
      " " +
      result.book +
      " " +
      result.author
    ).toLowerCase();

  const terms =
    cleanText(
      query
    )
      .toLowerCase()
      .split(
        /\s+/
      )
      .filter(
        (x) =>
          x.length >= 2
      );

  for (
    const term
    of terms
  ) {

    if (
      text.includes(
        term
      )
    ) {
      score += 2;
    }
  }

  if (
    result.text.length >= 200
  ) {
    score += 2;
  }

  if (
    result.text.length >= 500
  ) {
    score += 2;
  }

  if (
    result.book
  ) {
    score += 2;
  }

  if (
    result.author
  ) {
    score += 2;
  }

  if (
    result.page !== ""
  ) {
    score += 2;
  }

  if (
    result.book_id
  ) {
    score += 1;
  }

  if (
    result.url
  ) {
    score += 1;
  }

  return score;
}


/* =========================================================
   MULTI SEARCH
========================================================= */

async function multiSearch(
  queries,
  options = {}
) {

  const cleanQueries = [
    ...new Set(
      queries
        .map(
          (q) =>
            cleanText(q)
        )
        .filter(
          Boolean
        )
    ),
  ].slice(
    0,
    8
  );

  console.log(
    "\n========================================"
  );

  console.log(
    `🧠 MULTI SEARCH: ${cleanQueries.length} QUERIES`
  );

  console.log(
    "========================================"
  );

  const allResults = [];

  for (
    const query
    of cleanQueries
  ) {

    const results =
      await searchOneQuery(
        query,
        options
      );

    for (
      const result
      of results
    ) {

      allResults.push({
        ...result,

        _query:
          query,

        _score:
          scoreResult(
            result,
            query
          ),
      });
    }
  }

  console.log(
    `\n📚 TOTAL RAW NORMALIZED: ${allResults.length}`
  );

  const unique =
    deduplicate(
      allResults
    );

  console.log(
    `📚 AFTER DEDUPLICATE: ${unique.length}`
  );

  unique.sort(
    (a, b) =>
      b._score -
      a._score
  );

  const finalResults =
    unique
      .slice(
        0,
        MAX_FINAL_RESULTS
      )
      .map(
        (item) => ({
          text:
            item.text,

          book:
            item.book,

          author:
            item.author,

          page:
            item.page,

          book_id:
            item.book_id,

          url:
            item.url,

          category:
            item.category,
        })
      );

  console.log(
    `🏆 FINAL SOURCES: ${finalResults.length}`
  );

  finalResults.forEach(
    (
      source,
      index
    ) => {

      console.log(
        `\n[S${index + 1}]`
      );

      console.log(
        `📖 KITAB: ${
          source.book ||
          "TIADA"
        }`
      );

      console.log(
        `✍️ PENGARANG: ${
          source.author ||
          "TIADA"
        }`
      );

      console.log(
        `📄 HALAMAN: ${
          source.page ||
          "TIADA"
        }`
      );

      console.log(
        `🆔 BOOK ID: ${
          source.book_id ||
          "TIADA"
        }`
      );

      console.log(
        `🔗 URL: ${
          source.url ||
          "TIADA"
        }`
      );
    }
  );

  return finalResults;
}


/* =========================================================
   HTTP
========================================================= */

function sendJson(
  res,
  statusCode,
  data
) {

  const body =
    JSON.stringify(
      data,
      null,
      2
    );

  res.writeHead(
    statusCode,
    {
      "Content-Type":
        "application/json; charset=utf-8",

      "Content-Length":
        Buffer.byteLength(
          body
        ),
    }
  );

  res.end(
    body
  );
}


function parseBody(
  req
) {

  return new Promise(
    (
      resolve,
      reject
    ) => {

      let body = "";

      req.on(
        "data",
        (chunk) => {
          body += chunk;
        }
      );

      req.on(
        "end",
        () => {

          if (!body) {
            resolve({});
            return;
          }

          try {

            resolve(
              JSON.parse(
                body
              )
            );

          } catch {

            resolve({});
          }
        }
      );

      req.on(
        "error",
        reject
      );
    }
  );
}


/* =========================================================
   SERVER
========================================================= */

const server =
  http.createServer(
    async (
      req,
      res
    ) => {

      try {

        const url =
          new URL(
            req.url,
            `http://${HOST}:${PORT}`
          );

        /* ================================================
           HEALTH
        ================================================= */

        if (
          req.method === "GET" &&
          url.pathname === "/health"
        ) {

          return sendJson(
            res,
            200,
            {
              ok: true,
              service:
                "turath",
              status:
                "running",
              target_sources:
                MAX_FINAL_RESULTS,
            }
          );
        }


        /* ================================================
           CATEGORIES
        ================================================= */

        if (
          req.method === "GET" &&
          url.pathname === "/categories"
        ) {

          return sendJson(
            res,
            200,
            {
              ok: true,
              categories:
                CATEGORY_IDS,
              names:
                CATEGORY_NAMES_AR,
            }
          );
        }


        /* ================================================
           GET SEARCH
        ================================================= */

        if (
          req.method === "GET" &&
          url.pathname === "/search"
        ) {

          const query =
            url.searchParams.get(
              "q"
            ) ||
            url.searchParams.get(
              "query"
            ) ||
            "";

          const queriesParam =
            url.searchParams.get(
              "queries"
            );

          let queries = [];

          if (
            queriesParam
          ) {

            try {

              queries =
                JSON.parse(
                  queriesParam
                );

            } catch {

              queries = [];
            }
          }

          if (
            !queries.length &&
            query
          ) {

            queries = [
              query,
            ];
          }

          if (
            !queries.length
          ) {

            return sendJson(
              res,
              400,
              {
                ok: false,
                error:
                  "query diperlukan",
              }
            );
          }

          const category =
            url.searchParams.get(
              "category"
            ) ||
            "shafii";

          const categoryId =
            CATEGORY_IDS[
              category
            ] ||
            CATEGORY_IDS.shafii;

          const results =
            await multiSearch(
              queries,
              {
                category:
                  categoryId,
              }
            );

          return sendJson(
            res,
            200,
            {
              ok: true,

              query,

              queries,

              category,

              category_id:
                categoryId,

              count:
                results.length,

              results,

              sources:
                results,

              data:
                results,
            }
          );
        }


        /* ================================================
           POST SEARCH
        ================================================= */

        if (
          req.method === "POST" &&
          url.pathname === "/search"
        ) {

          const body =
            await parseBody(
              req
            );

          let queries = [];

          if (
            Array.isArray(
              body.queries
            )
          ) {

            queries =
              body.queries;

          } else if (
            body.query
          ) {

            queries = [
              body.query,
            ];
          }

          if (
            !queries.length
          ) {

            return sendJson(
              res,
              400,
              {
                ok: false,
                error:
                  "query atau queries diperlukan",
              }
            );
          }

          const category =
            body.category ||
            "shafii";

          const categoryId =
            CATEGORY_IDS[
              category
            ] ||
            CATEGORY_IDS.shafii;

          console.log(
            "\n📡 TURATH REQUEST:"
          );

          console.log(
            JSON.stringify(
              {
                queries,
                category,
              },
              null,
              2
            )
          );

          const results =
            await multiSearch(
              queries,
              {
                category:
                  categoryId,
              }
            );

          console.log(
            `\n📡 TURATH STATUS: 200`
          );

          console.log(
            `📚 TURATH SOURCES: ${
              results.length
            }`
          );

          return sendJson(
            res,
            200,
            {
              ok: true,

              query:
                body.query ||
                "",

              queries,

              category,

              category_id:
                categoryId,

              count:
                results.length,

              results,

              sources:
                results,

              data:
                results,
            }
          );
        }


        /* ================================================
           ROOT
        ================================================= */

        if (
          req.method === "GET" &&
          url.pathname === "/"
        ) {

          return sendJson(
            res,
            200,
            {
              ok: true,

              service:
                "TanyaFiqihBot Turath Service",

              version:
                "4.0",

              target_sources:
                MAX_FINAL_RESULTS,

              max_results_per_query:
                MAX_RESULTS_PER_QUERY,

              categories:
                CATEGORY_IDS,
            }
          );
        }


        return sendJson(
          res,
          404,
          {
            ok: false,
            error:
              "Not found",
          }
        );

      } catch (
        error
      ) {

        console.error(
          "❌ TURATH SERVER ERROR:",
          error
        );

        return sendJson(
          res,
          500,
          {
            ok: false,
            error:
              error.message,
          }
        );
      }
    }
  );


/* =========================================================
   START
========================================================= */

server.listen(
  PORT,
  HOST,
  () => {

    console.log(
      "=============================================="
    );

    console.log(
      "🚀 TURATH SERVICE STARTED"
    );

    console.log(
      `📡 http://${HOST}:${PORT}`
    );

    console.log(
      "📚 CATEGORY IDS:",
      CATEGORY_IDS
    );

    console.log(
      `🎯 TARGET SOURCES: ${
        MAX_FINAL_RESULTS
      }`
    );

    console.log(
      `🔎 RESULTS / QUERY: ${
        MAX_RESULTS_PER_QUERY
      }`
    );

    console.log(
      "=============================================="
    );
  }
);
