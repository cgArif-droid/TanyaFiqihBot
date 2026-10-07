import http from "http";
import { search, getBookInfo, getPage } from "turath-sdk";

/* =========================================================
   CONFIG
========================================================= */

const PORT = Number(process.env.TURATH_PORT || 8765);
const HOST = process.env.TURATH_HOST || "127.0.0.1";

const RESULTS_PER_PAGE = 20;
const MAX_PAGES = 10;

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
  "puasa ramadan": "صيام رمضان",
  "puasa": "الصيام",

  "zakat fitrah": "زكاة الفطر",
  "zakat": "الزكاة",

  "solat subuh": "صلاة الصبح",
  "solat jumaat": "صلاة الجمعة",
  "solat": "الصلاة",
  "sembahyang": "الصلاة",

  "qunut subuh": "القنوت في صلاة الصبح",
  "qunut": "القنوت",

  "wuduk": "الوضوء",
  "wudhu": "الوضوء",

  "mandi wajib": "الغسل",
  "mandi junub": "غسل الجنابة",
  "junub": "الجنابة",
  "bersuci": "الطهارة",
  "taharah": "الطهارة",
  "tayamum": "التيمم",

  "haid": "الحيض",
  "nifas": "النفاس",
  "istihadah": "الاستحاضة",

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

/* =========================================================
   CACHE
========================================================= */

const bookInfoCache = new Map();

/* =========================================================
   BASIC HELPERS
========================================================= */

function isObject(value) {
  return (
    value !== null &&
    typeof value === "object"
  );
}

function cleanText(value) {
  if (
    value === undefined ||
    value === null
  ) {
    return "";
  }

  if (
    typeof value !== "string" &&
    typeof value !== "number"
  ) {
    return "";
  }

  return String(value)
    .replace(/\s+/g, " ")
    .trim();
}

function firstValue(...values) {
  for (const value of values) {
    const cleaned = cleanText(value);

    if (cleaned) {
      return cleaned;
    }
  }

  return "";
}

/* =========================================================
   RECURSIVE VALUE FINDER
========================================================= */

function findValueRecursive(
  obj,
  keys,
  depth = 0
) {
  if (
    !isObject(obj) ||
    depth > 10
  ) {
    return "";
  }

  const wanted = new Set(
    keys.map((key) =>
      String(key).toLowerCase()
    )
  );

  if (Array.isArray(obj)) {
    for (const item of obj) {
      const found =
        findValueRecursive(
          item,
          keys,
          depth + 1
        );

      if (found) {
        return found;
      }
    }

    return "";
  }

  /*
   * PASS 1:
   * Cari exact key.
   */
  for (const [key, value] of Object.entries(obj)) {
    if (
      wanted.has(
        String(key).toLowerCase()
      )
    ) {
      const cleaned =
        cleanText(value);

      if (cleaned) {
        return cleaned;
      }
    }
  }

  /*
   * PASS 2:
   * Cari dalam object bersarang.
   */
  for (const value of Object.values(obj)) {
    if (isObject(value)) {
      const found =
        findValueRecursive(
          value,
          keys,
          depth + 1
        );

      if (found) {
        return found;
      }
    }
  }

  return "";
}

/* =========================================================
   FIND ANY POSSIBLE BOOK ID
========================================================= */

function extractBookId(item) {
  return firstValue(
    item?.book_id,
    item?.bookId,
    item?.bookID,
    item?.book_hash,
    item?.bookHash,
    item?.bookid,

    item?.book?.id,
    item?.book?.book_id,
    item?.book?.bookId,

    item?.metadata?.book_id,
    item?.metadata?.bookId,
    item?.metadata?.book_hash,

    item?.source?.book_id,
    item?.source?.bookId,

    findValueRecursive(
      item,
      [
        "book_id",
        "bookId",
        "bookID",
        "book_hash",
        "bookHash",
        "bookid",
      ]
    )
  );
}

/* =========================================================
   TEXT
========================================================= */

function extractText(item) {
  return firstValue(
    item?.text,
    item?.content,
    item?.passage,
    item?.snippet,
    item?.snip,
    item?.body,
    item?.description,

    item?.metadata?.text,
    item?.metadata?.content,

    findValueRecursive(
      item,
      [
        "text",
        "content",
        "passage",
        "snippet",
        "snip",
        "body",
      ]
    )
  );
}

/* =========================================================
   BOOK TITLE
========================================================= */

function extractBookTitle(item) {
  return firstValue(
    item?.book_title,
    item?.bookTitle,
    item?.book_name,
    item?.bookName,

    typeof item?.book === "string"
      ? item.book
      : "",

    item?.title,

    item?.book?.title,
    item?.book?.name,
    item?.book?.book_title,
    item?.book?.book_name,

    item?.metadata?.book_title,
    item?.metadata?.bookTitle,
    item?.metadata?.book_name,
    item?.metadata?.bookName,
    item?.metadata?.book,

    item?.source?.book_title,
    item?.source?.book_name,
    item?.source?.title,

    findValueRecursive(
      item,
      [
        "book_title",
        "bookTitle",
        "book_name",
        "bookName",
        "title",
      ]
    )
  );
}

/* =========================================================
   AUTHOR
========================================================= */

function extractAuthor(item) {
  return firstValue(
    item?.author,
    item?.author_name,
    item?.authorName,
    item?.book_author,

    item?.book?.author,
    item?.book?.author_name,
    item?.book?.authorName,

    item?.metadata?.author,
    item?.metadata?.author_name,
    item?.metadata?.authorName,

    item?.source?.author,

    findValueRecursive(
      item,
      [
        "author",
        "author_name",
        "authorName",
        "book_author",
      ]
    )
  );
}

/* =========================================================
   PAGE
========================================================= */

function extractPage(item) {
  return firstValue(
    item?.page,
    item?.page_number,
    item?.pageNumber,
    item?.page_no,
    item?.pageNo,
    item?.halaman,

    item?.location?.page,
    item?.metadata?.page,
    item?.metadata?.page_number,

    item?.source?.page,

    findValueRecursive(
      item,
      [
        "page",
        "page_number",
        "pageNumber",
        "page_no",
        "pageNo",
        "halaman",
      ]
    )
  );
}

/* =========================================================
   URL
========================================================= */

function extractUrl(item) {
  return firstValue(
    item?.url,
    item?.link,
    item?.href,

    item?.book?.url,
    item?.book?.link,

    item?.metadata?.url,
    item?.metadata?.link,

    findValueRecursive(
      item,
      [
        "url",
        "link",
        "href",
      ]
    )
  );
}

/* =========================================================
   RESULT ID
========================================================= */

function extractId(item) {
  return firstValue(
    item?.id,
    item?.chunk_id,
    item?.chunkId,

    item?.metadata?.id,
    item?.metadata?.chunk_id,

    findValueRecursive(
      item,
      [
        "id",
        "chunk_id",
        "chunkId",
      ]
    )
  );
}

/* =========================================================
   CATEGORY
========================================================= */

function extractCategory(item) {
  return firstValue(
    item?.category,
    item?.category_name,
    item?.categoryName,
    item?.mazhab,

    item?.metadata?.category,
    item?.metadata?.category_name,
    item?.metadata?.mazhab,

    findValueRecursive(
      item,
      [
        "category",
        "category_name",
        "categoryName",
        "mazhab",
      ]
    )
  );
}

/* =========================================================
   NORMALIZE BOOK INFO
========================================================= */

function normalizeBookInfo(
  info,
  bookId = ""
) {
  if (!info) {
    return null;
  }

  const title =
    extractBookTitle(info);

  const author =
    extractAuthor(info);

  const page =
    extractPage(info);

  const url =
    extractUrl(info);

  const extractedId =
    firstValue(
      bookId,
      extractBookId(info)
    );

  return {
    book: title,
    book_title: title,
    source: title,
    author,
    page,
    page_number: page,
    url,
    book_id: extractedId,
  };
}

/* =========================================================
   GET BOOK INFO
========================================================= */

async function fetchBookInfo(bookId) {
  if (!bookId) {
    return null;
  }

  if (
    bookInfoCache.has(
      bookId
    )
  ) {
    return bookInfoCache.get(
      bookId
    );
  }

  try {
    console.log(
      `📖 GET BOOK INFO: ${bookId}`
    );

    const info =
      await getBookInfo(
        bookId
      );

    console.log(
      "📖 BOOK INFO RAW:"
    );

    try {
      console.log(
        JSON.stringify(
          info,
          null,
          2
        ).slice(
          0,
          15000
        )
      );
    } catch {
      console.log(info);
    }

    const normalized =
      normalizeBookInfo(
        info,
        bookId
      );

    if (normalized) {
      bookInfoCache.set(
        bookId,
        normalized
      );
    }

    return normalized;

  } catch (error) {

    console.error(
      `❌ getBookInfo(${bookId}) ERROR:`,
      error?.message ||
        error
    );

    return null;
  }
}

/* =========================================================
   NORMALIZE RESULT
========================================================= */

async function normalizeResult(
  item,
  categoryKey
) {
  const text =
    extractText(item);

  if (!text) {
    return null;
  }

  let book =
    extractBookTitle(item);

  let author =
    extractAuthor(item);

  let page =
    extractPage(item);

  let url =
    extractUrl(item);

  let bookId =
    extractBookId(item);

  let resultId =
    extractId(item);

  /*
   * =======================================================
   * BOOK INFO
   * =======================================================
   *
   * Kalau search result ada book ID:
   * ambil maklumat kitab penuh.
   */
  let bookInfo = null;

  if (bookId) {
    bookInfo =
      await fetchBookInfo(
        bookId
      );
  }

  /*
   * Merge metadata.
   */
  if (bookInfo) {

    book =
      firstValue(
        book,
        bookInfo.book,
        bookInfo.book_title,
        bookInfo.source
      );

    author =
      firstValue(
        author,
        bookInfo.author
      );

    page =
      firstValue(
        page,
        bookInfo.page,
        bookInfo.page_number
      );

    url =
      firstValue(
        url,
        bookInfo.url
      );

    bookId =
      firstValue(
        bookId,
        bookInfo.book_id
      );
  }

  /*
   * Kalau masih tiada book ID,
   * cuba ambil ID daripada metadata selepas
   * proses book info.
   */
  if (!bookId) {
    bookId =
      extractBookId(
        item
      );
  }

  const category =
    firstValue(
      extractCategory(item),
      CATEGORY_NAMES_AR[
        categoryKey
      ]
    );

  return {
    text,

    book:
      cleanText(book),

    book_title:
      cleanText(book),

    source:
      cleanText(book),

    author:
      cleanText(author),

    page:
      cleanText(page),

    page_number:
      cleanText(page),

    category:
      cleanText(category),

    category_id:
      CATEGORY_IDS[
        categoryKey
      ],

    book_id:
      cleanText(bookId),

    id:
      cleanText(resultId),

    url:
      cleanText(url),

    origin:
      "turath",

    query:
      item?._query || "",

    raw_metadata: {
      book_id:
        cleanText(bookId),

      book:
        cleanText(book),

      author:
        cleanText(author),

      page:
        cleanText(page),

      url:
        cleanText(url),
    },
  };
}

/* =========================================================
   SEARCH ONE PAGE
========================================================= */

async function searchOnePage(
  query,
  categoryId,
  page
) {
  try {

    const response =
      await search(
        query,
        {
          category:
            categoryId,

          page,

          limit:
            RESULTS_PER_PAGE,
        }
      );

    let results = [];

    if (
      Array.isArray(
        response
      )
    ) {

      results =
        response;

    } else if (
      response &&
      Array.isArray(
        response.data
      )
    ) {

      results =
        response.data;

    } else if (
      response &&
      Array.isArray(
        response.results
      )
    ) {

      results =
        response.results;

    } else if (
      response &&
      Array.isArray(
        response.hits
      )
    ) {

      results =
        response.hits;
    }

    /*
     * Debug raw result.
     */
    if (
      page === 1 &&
      results.length > 0
    ) {

      console.log(
        "\n=============================================="
      );

      console.log(
        "🧪 FIRST RAW TURATH RESULT"
      );

      console.log(
        "=============================================="
      );

      try {

        console.log(
          JSON.stringify(
            results[0],
            null,
            2
          ).slice(
            0,
            30000
          )
        );

      } catch {

        console.log(
          results[0]
        );
      }

      console.log(
        "==============================================\n"
      );
    }

    return results.map(
      (result) => ({
        ...result,

        _query:
          query,

        _categoryId:
          categoryId,
      })
    );

  } catch (error) {

    console.error(
      `❌ SEARCH ERROR page=${page} category=${categoryId}:`,
      error?.message ||
        error
    );

    return [];
  }
}

/* =========================================================
   SEARCH CATEGORY
========================================================= */

async function searchCategory(
  query,
  categoryKey,
  maxResults = 10
) {
  const categoryId =
    CATEGORY_IDS[
      categoryKey
    ];

  if (!categoryId) {
    return [];
  }

  const all = [];

  for (
    let page = 1;
    page <= MAX_PAGES;
    page++
  ) {

    const results =
      await searchOnePage(
        query,
        categoryId,
        page
      );

    if (
      !results.length
    ) {
      break;
    }

    all.push(
      ...results
    );

    if (
      all.length >=
      maxResults
    ) {
      break;
    }
  }

  const sliced =
    all.slice(
      0,
      maxResults
    );

  const normalized = [];

  for (
    const item of sliced
  ) {

    const result =
      await normalizeResult(
        item,
        categoryKey
      );

    if (
      result &&
      result.text
    ) {

      normalized.push(
        result
      );
    }
  }

  return normalized;
}

/* =========================================================
   QUERY MAP
========================================================= */

function mapQuery(
  query
) {
  const lower =
    String(
      query || ""
    )
      .toLowerCase()
      .trim();

  const keys =
    Object.keys(
      QUERY_MAP
    ).sort(
      (a, b) =>
        b.length -
        a.length
    );

  for (
    const key of keys
  ) {

    if (
      lower.includes(
        key
      )
    ) {

      return QUERY_MAP[
        key
      ];
    }
  }

  return query;
}

/* =========================================================
   DEDUPLICATE
========================================================= */

function deduplicate(
  items
) {
  const seen =
    new Set();

  const output =
    [];

  for (
    const item of items
  ) {

    const key =
      [
        item.book_id,
        item.id,
        item.page,
        item.text
          ?.slice(
            0,
            150
          ),
      ]
        .join("|")
        .toLowerCase();

    if (
      seen.has(
        key
      )
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
   NORMAL SEARCH
========================================================= */

async function normalSearch(
  query
) {
  const arabicQuery =
    mapQuery(
      query
    );

  console.log(
    `🔎 SHAFII QUERY: ${arabicQuery}`
  );

  const results =
    await searchCategory(
      arabicQuery,
      "shafii",
      10
    );

  return deduplicate(
    results
  );
}

/* =========================================================
   COMPARISON SEARCH
========================================================= */

async function comparisonSearch(
  query
) {
  const arabicQuery =
    mapQuery(
      query
    );

  console.log(
    `⚖️ COMPARISON QUERY: ${arabicQuery}`
  );

  const shafii =
    await searchCategory(
      arabicQuery,
      "shafii",
      10
    );

  const hanafi =
    await searchCategory(
      arabicQuery,
      "hanafi",
      2
    );

  const maliki =
    await searchCategory(
      arabicQuery,
      "maliki",
      2
    );

  const hanbali =
    await searchCategory(
      arabicQuery,
      "hanbali",
      2
    );

  return deduplicate([
    ...shafii,
    ...hanafi,
    ...maliki,
    ...hanbali,
  ]);
}

/* =========================================================
   REQUEST BODY
========================================================= */

function readBody(
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
          resolve(
            body
          );
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
   JSON RESPONSE
========================================================= */

function sendJson(
  res,
  status,
  data
) {
  const output =
    JSON.stringify(
      data
    );

  res.writeHead(
    status,
    {
      "Content-Type":
        "application/json; charset=utf-8",

      "Access-Control-Allow-Origin":
        "*",

      "Access-Control-Allow-Methods":
        "GET,POST,OPTIONS",

      "Access-Control-Allow-Headers":
        "Content-Type",
    }
  );

  res.end(
    output
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

        /*
         * CORS
         */
        if (
          req.method ===
          "OPTIONS"
        ) {

          sendJson(
            res,
            204,
            {}
          );

          return;
        }

        const url =
          new URL(
            req.url,
            `http://${HOST}:${PORT}`
          );

        /* =================================================
           HOME
        ================================================= */

        if (
          req.method ===
            "GET" &&
          url.pathname ===
            "/"
        ) {

          sendJson(
            res,
            200,
            {
              success:
                true,

              service:
                "TURATH SERVICE",

              status:
                "running",

              port:
                PORT,

              categories:
                CATEGORY_IDS,
            }
          );

          return;
        }

        /* =================================================
           HEALTH
        ================================================= */

        if (
          req.method ===
            "GET" &&
          url.pathname ===
            "/health"
        ) {

          sendJson(
            res,
            200,
            {
              success:
                true,

              status:
                "healthy",

              categories:
                CATEGORY_IDS,
            }
          );

          return;
        }

        /* =================================================
           CATEGORIES
        ================================================= */

        if (
          req.method ===
            "GET" &&
          url.pathname ===
            "/categories"
        ) {

          sendJson(
            res,
            200,
            {
              success:
                true,

              categories:
                CATEGORY_IDS,

              names:
                CATEGORY_NAMES_AR,
            }
          );

          return;
        }

        /* =================================================
           SEARCH
        ================================================= */

        if (
          req.method ===
            "POST" &&
          url.pathname ===
            "/search"
        ) {

          const body =
            await readBody(
              req
            );

          let payload = {};

          try {

            payload =
              body
                ? JSON.parse(
                    body
                  )
                : {};

          } catch {

            sendJson(
              res,
              400,
              {
                success:
                  false,

                error:
                  "Invalid JSON",
              }
            );

            return;
          }

          const query =
            String(
              payload.query ||
              ""
            ).trim();

          const comparison =
            Boolean(
              payload.comparison
            );

          const requestedLimit =
            Number(
              payload.limit ||
              10
            );

          if (!query) {

            sendJson(
              res,
              400,
              {
                success:
                  false,

                error:
                  "query diperlukan",
              }
            );

            return;
          }

          console.log(
            "\n=============================================="
          );

          console.log(
            `🔎 REQUEST: ${query}`
          );

          console.log(
            `⚖️ COMPARISON: ${comparison}`
          );

          console.log(
            "==============================================\n"
          );

          let passages;

          if (
            comparison
          ) {

            passages =
              await comparisonSearch(
                query
              );

          } else {

            passages =
              await normalSearch(
                query
              );
          }

          if (
            requestedLimit > 0
          ) {

            passages =
              passages.slice(
                0,
                requestedLimit
              );
          }

          /*
           * =================================================
           * DEBUG FINAL
           * =================================================
           */

          console.log(
            `📚 TURATH RESULTS: ${passages.length}`
          );

          for (
            const item of passages.slice(
              0,
              10
            )
          ) {

            console.log(
              "📖 KITAB:",
              item.book ||
                "TIADA"
            );

            console.log(
              "✍️ PENGARANG:",
              item.author ||
                "TIADA"
            );

            console.log(
              "📄 HALAMAN:",
              item.page ||
                "TIADA"
            );

            console.log(
              "🆔 BOOK ID:",
              item.book_id ||
                "TIADA"
            );

            console.log(
              "🔗 URL:",
              item.url ||
                "TIADA"
            );

            console.log(
              "⚖️ KATEGORI:",
              item.category ||
                "TIADA"
            );

            console.log(
              "----------------------------------------------"
            );
          }

          sendJson(
            res,
            200,
            {
              success:
                true,

              mode:
                comparison
                  ? "comparison"
                  : "shafii",

              requested:
                query,

              query:
                query,

              count:
                passages.length,

              passages,
            }
          );

          return;
        }

        /* =================================================
           BOOK INFO
        ================================================= */

        const bookMatch =
          url.pathname.match(
            /^\/book\/(.+)$/
          );

        if (
          req.method ===
            "GET" &&
          bookMatch
        ) {

          const bookId =
            decodeURIComponent(
              bookMatch[1]
            );

          const info =
            await fetchBookInfo(
              bookId
            );

          if (!info) {

            sendJson(
              res,
              404,
              {
                success:
                  false,

                error:
                  "Maklumat kitab tidak ditemui",

                book_id:
                  bookId,
              }
            );

            return;
          }

          sendJson(
            res,
            200,
            {
              success:
                true,

              ...info,
            }
          );

          return;
        }

        /* =================================================
           PAGE
        ================================================= */

        const pageMatch =
          url.pathname.match(
            /^\/page\/([^/]+)\/([^/]+)$/
          );

        if (
          req.method ===
            "GET" &&
          pageMatch
        ) {

          const bookId =
            decodeURIComponent(
              pageMatch[1]
            );

          const page =
            decodeURIComponent(
              pageMatch[2]
            );

          try {

            const result =
              await getPage(
                bookId,
                page
              );

            sendJson(
              res,
              200,
              {
                success:
                  true,

                book_id:
                  bookId,

                page,

                data:
                  result,
              }
            );

          } catch (
            error
          ) {

            sendJson(
              res,
              500,
              {
                success:
                  false,

                error:
                  error?.message ||
                  String(
                    error
                  ),
              }
            );
          }

          return;
        }

        /* =================================================
           404
        ================================================= */

        sendJson(
          res,
          404,
          {
            success:
              false,

            error:
              "Endpoint tidak ditemui",
          }
        );

      } catch (
        error
      ) {

        console.error(
          "❌ SERVER ERROR:",
          error
        );

        sendJson(
          res,
          500,
          {
            success:
              false,

            error:
              error?.message ||
              String(
                error
              ),
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
      "=============================================="
    );
  }
);

/* =========================================================
   ERROR HANDLERS
========================================================= */

process.on(
  "unhandledRejection",
  (error) => {

    console.error(
      "❌ UNHANDLED REJECTION:",
      error
    );
  }
);

process.on(
  "uncaughtException",
  (error) => {

    console.error(
      "❌ UNCAUGHT EXCEPTION:",
      error
    );
  }
);
