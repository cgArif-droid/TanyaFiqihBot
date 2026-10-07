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
  puasa: "الصيام",
  puasa ramadan: "صيام رمضان",
  zakat: "الزكاة",
  zakat fitrah: "زكاة الفطر",
  solat: "الصلاة",
  sembahyang: "الصلاة",
  wuduk: "الوضوء",
  wudhu: "الوضوء",
  taharah: "الطهارة",
  bersuci: "الطهارة",
  tayamum: "التيمم",
  mandi wajib: "الغسل",
  junub: "الجنابة",
  haid: "الحيض",
  nifas: "النفاس",
  istihadah: "الاستحاضة",
  qunut: "القنوت",
  qunut subuh: "القنوت في صلاة الصبح",
  solat subuh: "صلاة الصبح",
  solat jumaat: "صلاة الجمعة",
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
  mas kahwin: "المهر",
  jual beli: "البيع",
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
   CACHE
========================================================= */

const bookInfoCache = new Map();

/* =========================================================
   BASIC HELPERS
========================================================= */

function firstValue(...values) {
  for (const value of values) {
    if (
      value !== undefined &&
      value !== null &&
      String(value).trim() !== ""
    ) {
      return value;
    }
  }

  return "";
}

function stringValue(value) {
  if (
    value === undefined ||
    value === null
  ) {
    return "";
  }

  if (
    typeof value === "string" ||
    typeof value === "number"
  ) {
    return String(value).trim();
  }

  return "";
}

function cleanText(value) {
  return stringValue(value)
    .replace(/\s+/g, " ")
    .trim();
}

function isObject(value) {
  return (
    value !== null &&
    typeof value === "object"
  );
}

/* =========================================================
   RECURSIVE SEARCH
========================================================= */

function findValueRecursive(
  obj,
  keys,
  depth = 0
) {
  if (
    !isObject(obj) ||
    depth > 8
  ) {
    return "";
  }

  const wanted = new Set(
    keys.map((x) =>
      String(x).toLowerCase()
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

      if (found !== "") {
        return found;
      }
    }

    return "";
  }

  for (const [key, value] of Object.entries(obj)) {
    if (
      wanted.has(
        String(key).toLowerCase()
      )
    ) {
      if (
        typeof value === "string" ||
        typeof value === "number"
      ) {
        return String(value).trim();
      }
    }
  }

  for (const value of Object.values(obj)) {
    if (isObject(value)) {
      const found =
        findValueRecursive(
          value,
          keys,
          depth + 1
        );

      if (found !== "") {
        return found;
      }
    }
  }

  return "";
}

/* =========================================================
   TEXT EXTRACTION
========================================================= */

function extractText(item) {
  return cleanText(
    firstValue(
      item?.text,
      item?.content,
      item?.snippet,
      item?.snip,
      item?.passage,
      item?.body,
      item?.description,

      findValueRecursive(
        item,
        [
          "text",
          "content",
          "snippet",
          "snip",
          "passage",
          "body",
        ]
      )
    )
  );
}

/* =========================================================
   BOOK ID
========================================================= */

function extractBookId(item) {
  return cleanText(
    firstValue(
      item?.book_id,
      item?.bookId,
      item?.bookID,
      item?.book_hash,
      item?.bookHash,

      findValueRecursive(
        item,
        [
          "book_id",
          "bookId",
          "bookID",
          "book_hash",
          "bookHash",
        ]
      )
    )
  );
}

/* =========================================================
   PAGE
========================================================= */

function extractPage(item) {
  return cleanText(
    firstValue(
      item?.page,
      item?.page_number,
      item?.pageNumber,
      item?.page_no,
      item?.pageNo,
      item?.halaman,

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
    )
  );
}

/* =========================================================
   BOOK TITLE
========================================================= */

function extractBookTitle(item) {
  return cleanText(
    firstValue(
      item?.book,
      item?.book_title,
      item?.bookTitle,
      item?.book_name,
      item?.bookName,
      item?.title,

      findValueRecursive(
        item,
        [
          "book",
          "book_title",
          "bookTitle",
          "book_name",
          "bookName",
          "title",
        ]
      )
    )
  );
}

/* =========================================================
   AUTHOR
========================================================= */

function extractAuthor(item) {
  return cleanText(
    firstValue(
      item?.author,
      item?.book_author,
      item?.author_name,
      item?.authorName,

      findValueRecursive(
        item,
        [
          "author",
          "book_author",
          "author_name",
          "authorName",
        ]
      )
    )
  );
}

/* =========================================================
   URL
========================================================= */

function extractUrl(item) {
  return cleanText(
    firstValue(
      item?.url,
      item?.link,
      item?.href,

      findValueRecursive(
        item,
        [
          "url",
          "link",
          "href",
        ]
      )
    )
  );
}

/* =========================================================
   ID
========================================================= */

function extractId(item) {
  return cleanText(
    firstValue(
      item?.id,
      item?.chunk_id,
      item?.chunkId,

      findValueRecursive(
        item,
        [
          "id",
          "chunk_id",
          "chunkId",
        ]
      )
    )
  );
}

/* =========================================================
   CATEGORY
========================================================= */

function extractCategory(item) {
  return cleanText(
    firstValue(
      item?.category,
      item?.category_name,
      item?.categoryName,
      item?.mazhab,

      findValueRecursive(
        item,
        [
          "category",
          "category_name",
          "categoryName",
          "mazhab",
        ]
      )
    )
  );
}

/* =========================================================
   NORMALIZE BOOK INFO
========================================================= */

function normalizeBookInfo(info, bookId = "") {
  if (!info) {
    return null;
  }

  const title = cleanText(
    firstValue(
      info?.book,
      info?.book_title,
      info?.bookTitle,
      info?.book_name,
      info?.bookName,
      info?.title,

      findValueRecursive(
        info,
        [
          "book",
          "book_title",
          "bookTitle",
          "book_name",
          "bookName",
          "title",
        ]
      )
    )
  );

  const author = cleanText(
    firstValue(
      info?.author,
      info?.book_author,
      info?.author_name,
      info?.authorName,

      findValueRecursive(
        info,
        [
          "author",
          "book_author",
          "author_name",
          "authorName",
        ]
      )
    )
  );

  const url = cleanText(
    firstValue(
      info?.url,
      info?.link,
      info?.href,

      findValueRecursive(
        info,
        [
          "url",
          "link",
          "href",
        ]
      )
    )
  );

  const page = cleanText(
    firstValue(
      info?.page,
      info?.page_number,
      info?.pageNumber,

      findValueRecursive(
        info,
        [
          "page",
          "page_number",
          "pageNumber",
        ]
      )
    )
  );

  return {
    book: title,
    book_title: title,
    author,
    url,
    page,
    book_id: bookId,
  };
}

/* =========================================================
   GET BOOK INFO
========================================================= */

async function fetchBookInfo(bookId) {
  if (!bookId) {
    return null;
  }

  if (bookInfoCache.has(bookId)) {
    return bookInfoCache.get(bookId);
  }

  try {
    console.log(
      `📖 GET BOOK INFO: ${bookId}`
    );

    const info =
      await getBookInfo(bookId);

    console.log(
      "📖 BOOK INFO RAW:"
    );

    try {
      console.log(
        JSON.stringify(
          info,
          null,
          2
        ).slice(0, 10000)
      );
    } catch {
      console.log(info);
    }

    const normalized =
      normalizeBookInfo(
        info,
        bookId
      );

    bookInfoCache.set(
      bookId,
      normalized
    );

    return normalized;
  } catch (error) {
    console.error(
      `❌ getBookInfo(${bookId}) ERROR:`,
      error?.message || error
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

  const bookId =
    extractBookId(item);

  let book =
    extractBookTitle(item);

  let author =
    extractAuthor(item);

  let page =
    extractPage(item);

  let url =
    extractUrl(item);

  /*
   * Jika search result tak ada nama kitab,
   * cuba ambil melalui getBookInfo()
   */
  let bookInfo = null;

  if (bookId) {
    bookInfo =
      await fetchBookInfo(bookId);
  }

  if (bookInfo) {
    book =
      firstValue(
        book,
        bookInfo.book,
        bookInfo.book_title
      );

    author =
      firstValue(
        author,
        bookInfo.author
      );

    url =
      firstValue(
        url,
        bookInfo.url
      );

    if (!page) {
      page =
        firstValue(
          bookInfo.page
        );
    }
  }

  const id =
    extractId(item);

  const category =
    firstValue(
      extractCategory(item),
      CATEGORY_NAMES_AR[
        categoryKey
      ],
      categoryKey
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
      cleanText(id),

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
          category: categoryId,
          page,
          limit:
            RESULTS_PER_PAGE,
        }
      );

    let results = [];

    if (Array.isArray(response)) {
      results = response;
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

    if (
      page === 1 &&
      results.length > 0
    ) {
      console.log(
        "\n🧪 FIRST RAW TURATH RESULT:"
      );

      try {
        console.log(
          JSON.stringify(
            results[0],
            null,
            2
          ).slice(0, 20000)
        );
      } catch {
        console.log(
          results[0]
        );
      }

      console.log(
        "\n======================================\n"
      );
    }

    return results.map(
      (result) => ({
        ...result,
        _query: query,
        _categoryId:
          categoryId,
      })
    );
  } catch (error) {
    console.error(
      `❌ SEARCH ERROR page=${page} category=${categoryId}:`,
      error?.message || error
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

  const normalized =
    [];

  for (
    const item of sliced
  ) {
    const result =
      await normalizeResult(
        item,
        categoryKey
      );

    if (
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
   SEARCH QUERY MAP
========================================================= */

function mapQuery(query) {
  const lower =
    String(query || "")
      .toLowerCase()
      .trim();

  /*
   * Cari frasa paling panjang dahulu
   */
  const keys =
    Object.keys(
      QUERY_MAP
    ).sort(
      (a, b) =>
        b.length -
        a.length
    );

  for (const key of keys) {
    if (
      lower.includes(key)
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
          ?.slice(0, 120),
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
   SEARCH NORMAL
========================================================= */

async function normalSearch(
  query
) {
  const arabicQuery =
    mapQuery(query);

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
    mapQuery(query);

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
          resolve(body);
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
         * CORS preflight
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

        /* ===============================================
           HOME
        =============================================== */

        if (
          req.method ===
            "GET" &&
          url.pathname === "/"
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

        /* ===============================================
           HEALTH
        =============================================== */

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

        /* ===============================================
           CATEGORIES
        =============================================== */

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

        /* ===============================================
           SEARCH
        =============================================== */

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

          let payload =
            {};

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
            "\n======================================"
          );

          console.log(
            `🔎 REQUEST: ${query}`
          );

          console.log(
            `⚖️ COMPARISON: ${comparison}`
          );

          console.log(
            "======================================\n"
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

          /*
           * Hadkan hasil terakhir
           */
          if (
            requestedLimit >
            0
          ) {
            passages =
              passages.slice(
                0,
                requestedLimit
              );
          }

          /*
           * Debug metadata
           */
          console.log(
            `📚 TURATH RESULTS: ${passages.length}`
          );

          for (
            const item of passages.slice(
              0,
              5
            )
          ) {
            console.log(
              "📖 SOURCE:",
              item.book ||
                item.book_title ||
                "TIADA"
            );

            console.log(
              "✍️ AUTHOR:",
              item.author ||
                "TIADA"
            );

            console.log(
              "📄 PAGE:",
              item.page ||
                "TIADA"
            );

            console.log(
              "🆔 BOOK ID:",
              item.book_id ||
                "TIADA"
            );

            console.log(
              "----------------------------------"
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

        /* ===============================================
           BOOK INFO
        =============================================== */

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

        /* ===============================================
           PAGE
        =============================================== */

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

        /* ===============================================
           404
        =============================================== */

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
