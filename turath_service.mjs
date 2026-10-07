import http from "http";
import { search, getBookInfo, getPage } from "turath-sdk";

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
   GENERIC HELPERS
========================================================= */

function cleanText(value) {
  if (
    value === undefined ||
    value === null
  ) {
    return "";
  }

  if (
    typeof value === "string"
  ) {
    return value
      .replace(/\s+/g, " ")
      .trim();
  }

  return String(value)
    .replace(/\s+/g, " ")
    .trim();
}


function firstValue(obj, keys = []) {
  if (
    !obj ||
    typeof obj !== "object"
  ) {
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
   DEEP TEXT EXTRACTOR
========================================================= */

function findTextDeep(
  obj,
  depth = 0
) {
  if (
    depth > 8 ||
    obj === null ||
    obj === undefined
  ) {
    return "";
  }

  if (
    typeof obj === "string"
  ) {
    const text = cleanText(obj);

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
   METADATA EXTRACTORS
========================================================= */

function extractPage(obj) {
  if (!obj) {
    return "";
  }

  const value =
    firstValue(obj, [
      "page",
      "page_number",
      "pageNumber",
      "page_no",
      "pageNo",
    ]);

  if (
    value !== null &&
    value !== undefined
  ) {
    return value;
  }

  if (
    obj.meta
  ) {
    const metaValue =
      firstValue(
        obj.meta,
        [
          "page",
          "page_number",
          "pageNumber",
          "page_no",
          "pageNo",
        ]
      );

    if (
      metaValue !== null &&
      metaValue !== undefined
    ) {
      return metaValue;
    }
  }

  return "";
}


function extractBookId(obj) {
  if (!obj) {
    return "";
  }

  const value =
    firstValue(obj, [
      "book_id",
      "bookId",
      "bookid",
      "id_book",
    ]);

  if (
    value !== null &&
    value !== undefined
  ) {
    return value;
  }

  if (
    obj.meta
  ) {
    return (
      firstValue(
        obj.meta,
        [
          "book_id",
          "bookId",
          "bookid",
        ]
      ) || ""
    );
  }

  return "";
}


function extractBook(obj) {
  if (!obj) {
    return "";
  }

  let value =
    firstValue(obj, [
      "book",
      "book_name",
      "bookName",
      "title",
      "name",
    ]);

  if (
    typeof value === "object"
  ) {
    value =
      firstValue(
        value,
        [
          "name",
          "title",
          "book_name",
          "bookName",
        ]
      );
  }

  return cleanText(value);
}


function extractAuthor(obj) {
  if (!obj) {
    return "";
  }

  let value =
    firstValue(obj, [
      "author",
      "author_name",
      "authorName",
    ]);

  if (
    typeof value === "object"
  ) {
    value =
      firstValue(
        value,
        [
          "name",
          "author_name",
          "authorName",
        ]
      );
  }

  return cleanText(value);
}


function extractUrl(obj) {
  if (!obj) {
    return "";
  }

  const value =
    firstValue(obj, [
      "url",
      "link",
      "href",
      "book_url",
      "bookUrl",
      "source_url",
      "sourceUrl",
    ]);

  return cleanText(value);
}


/* =========================================================
   URL
========================================================= */

/*
 * Jangan reka URL jika book_id tiada.
 *
 * Jika SDK sendiri beri URL,
 * kita utamakan URL SDK.
 *
 * Jika tiada tetapi book_id ada,
 * kita gunakan halaman kitab Turath
 * berdasarkan ID kitab.
 */

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
   GET BOOK INFO
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
   * Kalau item mempunyai nested book object
   */
  if (
    !book &&
    item?.book
  ) {
    book =
      extractBook(
        item.book
      );
  }

  if (
    !author &&
    item?.book
  ) {
    author =
      extractAuthor(
        item.book
      );
  }

  if (
    !bookId &&
    item?.book
  ) {
    bookId =
      extractBookId(
        item.book
      );
  }

  /*
   * Ambil info kitab jika book_id ada.
   */

  if (bookId) {

    try {

      console.log(
        `📖 GET BOOK INFO: ${bookId}`
      );

      const info =
        await getBookInfo(
          bookId
        );

      if (info) {

        if (!book) {
          book =
            extractBook(info);
        }

        if (!author) {
          author =
            extractAuthor(info);
        }

        if (!page) {
          page =
            extractPage(info);
        }

        if (!url) {
          url =
            extractUrl(info);
        }

        /*
         * Debug struktur info
         */

        console.log(
          "🧪 BOOK INFO KEYS:",
          typeof info === "object"
            ? Object.keys(info)
            : []
        );
      }

    } catch (error) {

      console.log(
        `⚠️ GET BOOK INFO ERROR ${bookId}:`,
        error.message
      );
    }
  }

  /*
   * Kalau SDK tidak beri URL,
   * tetapi book_id wujud,
   * bina URL kitab.
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

  const text =
    findTextDeep(item);

  return {
    text,

    book:
      cleanText(book),

    author:
      cleanText(author),

    page:
      page === null ||
      page === undefined
        ? ""
        : page,

    book_id:
      bookId || "",

    url:
      cleanText(url),

    category:
      item?.category ||
      item?.category_name ||
      item?.categoryName ||
      "",

    raw: item,
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

  /*
   * Array terus
   */

  if (
    Array.isArray(raw)
  ) {
    return raw;
  }

  /*
   * FORMAT RASMI SEARCH RESULTS
   *
   * {
   *   count: ...,
   *   data: [...]
   * }
   */

  if (
    Array.isArray(
      raw.data
    )
  ) {
    return raw.data;
  }

  /*
   * Fallback
   */

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

  /*
   * Nested data
   */

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
   TURATH SDK SEARCH
========================================================= */

/*
 * INI PEMBETULAN UTAMA.
 *
 * turath-sdk menggunakan:
 *
 * search(query, {
 *   category: 16,
 *   page: 1
 * })
 *
 * BUKAN:
 *
 * categoryId
 */

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

    /*
     * DEBUG
     */

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
        Object.keys(result)
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

      /*
       * Cetak data mentah sedikit sahaja
       * supaya log tidak terlalu panjang.
       */

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

  } catch (error) {

    console.error(
      `❌ TURATH SEARCH ERROR:`,
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

  for (
    const item of items
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

    } catch (error) {

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
    const item of results
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

    seen.add(key);

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
    cleanText(query)
      .toLowerCase()
      .split(/\s+/)
      .filter(
        (x) =>
          x.length >= 2
      );

  for (
    const term of terms
  ) {

    if (
      text.includes(term)
    ) {
      score += 2;
    }
  }

  /*
   * Kandungan panjang
   */

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

  /*
   * Metadata
   */

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
        .filter(Boolean)
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

  /*
   * Duplicate
   */

  const unique =
    deduplicate(
      allResults
    );

  console.log(
    `📚 AFTER DEDUPLICATE: ${unique.length}`
  );

  /*
   * Ranking
   */

  unique.sort(
    (a, b) =>
      b._score -
      a._score
  );

  /*
   * Maksimum 10
   */

  const finalResults =
    unique
      .slice(
        0,
        MAX_FINAL_RESULTS
      )
      .map(
        (item) => {

          const result = {
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
          };

          return result;
        }
      );

  console.log(
    `🏆 FINAL SOURCES: ${finalResults.length}`
  );

  /*
   * Paparkan final sources
   */

  finalResults.forEach(
    (source, index) => {

      console.log(
        `\n[S${index + 1}]`
      );

      console.log(
        `📖 KITAB: ${source.book || "TIADA"}`
      );

      console.log(
        `✍️ PENGARANG: ${source.author || "TIADA"}`
      );

      console.log(
        `📄 HALAMAN: ${source.page || "TIADA"}`
      );

      console.log(
        `🆔 BOOK ID: ${source.book_id || "TIADA"}`
      );

      console.log(
        `🔗 URL: ${source.url || "TIADA"}`
      );

      console.log(
        `📝 TEXT: ${source.text.slice(
          0,
          500
        )}`
      );
    }
  );

  return finalResults;
}


/* =========================================================
   HTTP HELPERS
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
    (resolve, reject) => {

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
   HTTP SERVER
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
                categoryId:
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
                categoryId:
                  categoryId,
              }
            );

          console.log(
            `\n📡 TURATH STATUS: 200`
          );

          console.log(
            `📚 TURATH SOURCES: ${results.length}`
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
                "3.0",

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

      } catch (error) {

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
      `🎯 TARGET SOURCES: ${MAX_FINAL_RESULTS}`
    );

    console.log(
      `🔎 RESULTS / QUERY: ${MAX_RESULTS_PER_QUERY}`
    );

    console.log(
      "=============================================="
    );
  }
);
