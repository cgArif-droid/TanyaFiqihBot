import http from "http";

import {
  search,
  getBookInfo,
  getPage,
} from "turath-sdk";

// ============================================================
// CONFIG
// ============================================================

const PORT = Number(
  process.env.TURATH_PORT || 8765
);

const HOST =
  process.env.TURATH_HOST ||
  "127.0.0.1";

const SEARCH_PAGES = Number(
  process.env.TURATH_SEARCH_PAGES || 10
);

const RESULTS_PER_PAGE = Number(
  process.env.TURATH_RESULTS_PER_PAGE || 20
);

// ============================================================
// CATEGORY IDS
// ============================================================

const CATEGORY_IDS = {
  hanafi: Number(
    process.env.TURATH_HANAFI_CATEGORY_ID || 14
  ),

  maliki: Number(
    process.env.TURATH_MALIKI_CATEGORY_ID || 15
  ),

  shafii: Number(
    process.env.TURATH_SHAFII_CATEGORY_ID || 16
  ),

  hanbali: Number(
    process.env.TURATH_HANBALI_CATEGORY_ID || 17
  ),
};

// ============================================================
// CATEGORY NAMES
// ============================================================

const CATEGORY_NAMES = {
  hanafi: "الحنفية",
  maliki: "المالكية",
  shafii: "الشافعية",
  hanbali: "الحنابلة",
};

// ============================================================
// LIMITS
// ============================================================

const NORMAL_LIMITS = {
  shafii: 10,
};

const COMPARISON_LIMITS = {
  shafii: 10,
  hanafi: 2,
  maliki: 2,
  hanbali: 2,
};

// ============================================================
// QUERY MAP
// ============================================================

const QUERY_MAP = {

  // ----------------------------------------------------------
  // WUDUK
  // ----------------------------------------------------------

  wuduk: [
    "الوضوء",
    "وضوء",
    "أحكام الوضوء",
  ],

  "air sembahyang": [
    "الوضوء",
    "أحكام الوضوء",
  ],

  // ----------------------------------------------------------
  // SOLAT
  // ----------------------------------------------------------

  solat: [
    "الصلاة",
    "أحكام الصلاة",
    "صلاة",
  ],

  sembahyang: [
    "الصلاة",
    "أحكام الصلاة",
    "صلاة",
  ],

  // ----------------------------------------------------------
  // QUNUT
  // ----------------------------------------------------------

  qunut: [
    "القنوت",
    "قنوت الفجر",
    "القنوت في صلاة الصبح",
    "دعاء القنوت",
  ],

  // ----------------------------------------------------------
  // PUASA
  // ----------------------------------------------------------

  puasa: [
    "الصيام",
    "أحكام الصيام",
    "الصوم",
  ],

  // ----------------------------------------------------------
  // ZAKAT
  // ----------------------------------------------------------

  zakat: [
    "الزكاة",
    "أحكام الزكاة",
  ],

  // ----------------------------------------------------------
  // HAJI
  // ----------------------------------------------------------

  haji: [
    "الحج",
    "أحكام الحج",
  ],

  // ----------------------------------------------------------
  // UMRAH
  // ----------------------------------------------------------

  umrah: [
    "العمرة",
    "أحكام العمرة",
  ],

  // ----------------------------------------------------------
  // TAYAMMUM
  // ----------------------------------------------------------

  tayammum: [
    "التيمم",
    "أحكام التيمم",
  ],

  // ----------------------------------------------------------
  // NAJIS
  // ----------------------------------------------------------

  najis: [
    "النجاسة",
    "أحكام النجاسة",
  ],

  // ----------------------------------------------------------
  // TAHARAH
  // ----------------------------------------------------------

  bersuci: [
    "الطهارة",
    "أحكام الطهارة",
  ],

  taharah: [
    "الطهارة",
    "أحكام الطهارة",
  ],

  // ----------------------------------------------------------
  // NIKAH
  // ----------------------------------------------------------

  nikah: [
    "النكاح",
    "أحكام النكاح",
  ],

  kahwin: [
    "النكاح",
    "أحكام النكاح",
  ],

  // ----------------------------------------------------------
  // TALAK
  // ----------------------------------------------------------

  talak: [
    "الطلاق",
    "أحكام الطلاق",
  ],

  cerai: [
    "الطلاق",
    "أحكام الطلاق",
  ],

  // ----------------------------------------------------------
  // FARAID
  // ----------------------------------------------------------

  faraid: [
    "الفرائض",
    "الميراث",
    "أحكام المواريث",
  ],

  pusaka: [
    "الميراث",
    "التركة",
    "أحكام المواريث",
  ],

  // ----------------------------------------------------------
  // JUAL BELI
  // ----------------------------------------------------------

  jual: [
    "البيع",
    "أحكام البيع",
  ],

  beli: [
    "البيع",
    "أحكام البيع",
  ],

  // ----------------------------------------------------------
  // RIBA
  // ----------------------------------------------------------

  riba: [
    "الربا",
    "أحكام الربا",
  ],

  // ----------------------------------------------------------
  // HUTANG
  // ----------------------------------------------------------

  hutang: [
    "الدين",
    "أحكام الديون",
  ],

  // ----------------------------------------------------------
  // PINJAMAN
  // ----------------------------------------------------------

  pinjaman: [
    "القرض",
    "أحكام القرض",
  ],

  // ----------------------------------------------------------
  // KORBAN
  // ----------------------------------------------------------

  korban: [
    "الأضحية",
    "أحكام الأضحية",
  ],

  // ----------------------------------------------------------
  // AQIQAH
  // ----------------------------------------------------------

  aqiqah: [
    "العقيقة",
    "أحكام العقيقة",
  ],

  // ----------------------------------------------------------
  // QASAR
  // ----------------------------------------------------------

  qasar: [
    "القصر",
    "صلاة القصر",
  ],

  // ----------------------------------------------------------
  // JAMAK
  // ----------------------------------------------------------

  jamak: [
    "الجمع",
    "صلاة الجمع",
  ],

  // ----------------------------------------------------------
  // MUSAFIR
  // ----------------------------------------------------------

  musafir: [
    "السفر",
    "أحكام السفر",
  ],

  // ----------------------------------------------------------
  // AURAT
  // ----------------------------------------------------------

  aurat: [
    "العورة",
    "أحكام العورة",
  ],

  // ----------------------------------------------------------
  // HAID
  // ----------------------------------------------------------

  haid: [
    "الحيض",
    "أحكام الحيض",
  ],

  // ----------------------------------------------------------
  // NIFAS
  // ----------------------------------------------------------

  nifas: [
    "النفاس",
    "أحكام النفاس",
  ],

  // ----------------------------------------------------------
  // ISTIHADAH
  // ----------------------------------------------------------

  istihadah: [
    "الاستحاضة",
    "أحكام الاستحاضة",
  ],
};

// ============================================================
// MADHHAB COMPARISON
// ============================================================

function isMadhhabComparison(text) {

  if (!text) {
    return false;
  }

  const q = String(text)
    .trim()
    .toLowerCase();

  const explicit = [
    "perbandingan mazhab",
    "banding mazhab",
    "bandingkan mazhab",
    "beza mazhab",
    "perbezaan mazhab",
    "mengikut semua mazhab",
    "menurut semua mazhab",
    "semua mazhab",
    "empat mazhab",
    "keempat-empat mazhab",
    "compare mazhab",
    "compare madhhab",
    "madhhab comparison",
  ];

  if (
    explicit.some(
      phrase => q.includes(phrase)
    )
  ) {
    return true;
  }

  const madhhabNames = [
    ["syafie", "syafii", "syafi'i", "shafii", "shafi'i", "شافعي"],
    ["hanafi", "حنفي"],
    ["maliki", "مالكي"],
    ["hanbali", "حنبلي"],
  ];

  let count = 0;

  for (const names of madhhabNames) {

    if (
      names.some(
        name => q.includes(name)
      )
    ) {
      count++;
    }
  }

  return count >= 2;
}

// ============================================================
// ARABIC QUERY EXPANSION
// ============================================================

function arabicQueries(originalQuery) {

  const original =
    String(originalQuery || "").trim();

  const lower =
    original.toLowerCase();

  const queries = [];

  // Original query
  if (original) {
    queries.push(original);
  }

  // ----------------------------------------------------------
  // Detect mapped Malay keywords
  // ----------------------------------------------------------

  for (const [
    keyword,
    arabicList,
  ] of Object.entries(QUERY_MAP)) {

    if (lower.includes(keyword)) {

      for (const arabic of arabicList) {

        if (!queries.includes(arabic)) {
          queries.push(arabic);
        }
      }
    }
  }

  // ----------------------------------------------------------
  // If original already contains Arabic
  // ----------------------------------------------------------

  const hasArabic =
    /[\u0600-\u06FF]/.test(original);

  if (hasArabic) {

    if (!queries.includes(original)) {
      queries.push(original);
    }
  }

  return queries;
}

// ============================================================
// SEARCH ONE PAGE
// ============================================================

async function searchOnePage(
  query,
  categoryId,
  page
) {

  try {

    const response = await search(
      query,
      {
        category: categoryId,
        page,
        limit: RESULTS_PER_PAGE,
      }
    );

    let results = [];

    if (Array.isArray(response)) {
      results = response;
    }

    else if (
      response &&
      Array.isArray(response.data)
    ) {
      results = response.data;
    }

    else if (
      response &&
      Array.isArray(response.results)
    ) {
      results = response.results;
    }

    else if (
      response &&
      Array.isArray(response.hits)
    ) {
      results = response.hits;
    }

    console.log(
      `🔎 ${categoryId} | "${query}" | page=${page} | ${results.length} results`
    );

    return results;

  } catch (error) {

    console.error(
      `❌ SEARCH ERROR | category=${categoryId} | page=${page}`
    );

    console.error(error);

    return [];
  }
}

// ============================================================
// SEARCH CATEGORY MANY
// ============================================================

async function searchCategoryMany(
  categoryId,
  queries,
  maxResults
) {

  const collected = [];

  for (
    const query of queries
  ) {

    for (
      let page = 1;
      page <= SEARCH_PAGES;
      page++
    ) {

      const results =
        await searchOnePage(
          query,
          categoryId,
          page
        );

      if (!results.length) {
        break;
      }

      for (
        const result of results
      ) {

        collected.push({
          ...result,
          _query: query,
          _categoryId: categoryId,
        });

        if (
          collected.length >= maxResults
        ) {
          return collected;
        }
      }
    }
  }

  return collected;
}

// ============================================================
// COLLECT CATEGORY
// ============================================================

async function collectCategory(
  madhhab,
  queries,
  maxResults
) {

  const categoryId =
    CATEGORY_IDS[madhhab];

  if (!categoryId) {

    console.log(
      `⚠️ CATEGORY ID MISSING: ${madhhab}`
    );

    return [];
  }

  console.log(
    `📘 CATEGORY: ${madhhab} | ID=${categoryId}`
  );

  const results =
    await searchCategoryMany(
      categoryId,
      queries,
      maxResults
    );

  console.log(
    `📚 ${madhhab} collected = ${results.length}`
  );

  return results;
}

// ============================================================
// NORMAL SEARCH
// ============================================================

async function searchNormal(
  originalQuery
) {

  const queries =
    arabicQueries(
      originalQuery
    );

  console.log(
    "📘 NORMAL MODE: SYAFII"
  );

  console.log(
    "🔍 shafii queries:",
    queries
  );

  const results =
    await collectCategory(
      "shafii",
      queries,
      NORMAL_LIMITS.shafii
    );

  console.log(
    `📦 shafii TOTAL UNIQUE = ${results.length}`
  );

  return results;
}

// ============================================================
// COMPARISON SEARCH
// ============================================================

async function searchComparison(
  originalQuery
) {

  const queries =
    arabicQueries(
      originalQuery
    );

  console.log(
    "📘 COMPARISON MODE"
  );

  console.log(
    "🔍 queries:",
    queries
  );

  const allResults = [];

  for (
    const madhhab of [
      "shafii",
      "hanafi",
      "maliki",
      "hanbali",
    ]
  ) {

    const limit =
      COMPARISON_LIMITS[madhhab];

    const results =
      await collectCategory(
        madhhab,
        queries,
        limit
      );

    for (
      const result of results
    ) {

      allResults.push({
        ...result,
        madhhab,
        madhhab_name:
          CATEGORY_NAMES[madhhab],
      });
    }
  }

  return allResults;
}

// ============================================================
// DEDUPLICATE RESULTS
// ============================================================

function deduplicateResults(
  results
) {

  const seen = new Set();
  const unique = [];

  for (
    const item of results
  ) {

    const text =
      String(
        item.text ||
        item.content ||
        item.snippet ||
        ""
      ).trim();

    const book =
      String(
        item.book ||
        item.book_title ||
        item.title ||
        ""
      ).trim();

    const page =
      String(
        item.page ||
        item.page_number ||
        ""
      ).trim();

    const key =
      `${book}|${page}|${text.slice(0, 300)}`;

    if (seen.has(key)) {
      continue;
    }

    seen.add(key);

    unique.push(item);
  }

  return unique;
}

// ============================================================
// NORMALIZE RESULT
// ============================================================

function normalizeResult(
  item,
  fallbackCategory = null
) {

  if (!item) {
    return null;
  }

  const text =
    String(
      item.text ||
      item.content ||
      item.snippet ||
      item.snip ||
      ""
    ).trim();

  if (!text) {
    return null;
  }

  return {
    text,

    book:
      item.book ||
      item.book_title ||
      item.title ||
      "",

    page:
      item.page ||
      item.page_number ||
      "",

    category:
      item.category ||
      item.category_name ||
      item.mazhab ||
      fallbackCategory ||
      "",

    book_id:
      item.book_id ||
      item.bookId ||
      "",

    id:
      item.id ||
      item.chunk_id ||
      "",

    url:
      item.url ||
      "",
  };
}

// ============================================================
// HTTP SERVER
// ============================================================

const server =
  http.createServer(
    async (req, res) => {

      // --------------------------------------------------------
      // CORS
      // --------------------------------------------------------

      res.setHeader(
        "Access-Control-Allow-Origin",
        "*"
      );

      res.setHeader(
        "Access-Control-Allow-Headers",
        "Content-Type"
      );

      res.setHeader(
        "Content-Type",
        "application/json; charset=utf-8"
      );

      // --------------------------------------------------------
      // ROOT
      // --------------------------------------------------------

      if (
        req.method === "GET" &&
        req.url === "/"
      ) {

        res.writeHead(200);

        res.end(
          JSON.stringify({
            success: true,
            service: "Turath Search Service",
            status: "running",
          })
        );

        return;
      }

      // --------------------------------------------------------
      // HEALTH
      // --------------------------------------------------------

      if (
        req.method === "GET" &&
        req.url === "/health"
      ) {

        res.writeHead(200);

        res.end(
          JSON.stringify({
            success: true,
            status: "ok",
            port: PORT,
            host: HOST,
          })
        );

        return;
      }

      // --------------------------------------------------------
      // CATEGORIES
      // --------------------------------------------------------

      if (
        req.method === "GET" &&
        req.url === "/categories"
      ) {

        res.writeHead(200);

        res.end(
          JSON.stringify({
            success: true,
            categories:
              CATEGORY_IDS,
          })
        );

        return;
      }

      // --------------------------------------------------------
      // SEARCH
      // --------------------------------------------------------

      if (
        req.method === "POST" &&
        req.url === "/search"
      ) {

        try {

          let body = "";

          req.on(
            "data",
            chunk => {
              body += chunk.toString();
            }
          );

          req.on(
            "end",
            async () => {

              try {

                const payload =
                  JSON.parse(body || "{}");

                const query =
                  String(
                    payload.query ||
                    payload.question ||
                    ""
                  ).trim();

                const comparison =
                  typeof payload.comparison === "boolean"
                    ? payload.comparison
                    : isMadhhabComparison(
                        query
                      );

                console.log("");
                console.log("================================================");
                console.log("📚 TURATH SEARCH");
                console.log(`❓ ${query}`);
                console.log(
                  `🧭 MODE = ${
                    comparison
                      ? "COMPARISON"
                      : "SHAFII"
                  }`
                );
                console.log("================================================");

                if (!query) {

                  res.writeHead(400);

                  res.end(
                    JSON.stringify({
                      success: false,
                      error: "Query kosong",
                    })
                  );

                  return;
                }

                let rawResults = [];

                // ------------------------------------------------
                // SEARCH MODE
                // ------------------------------------------------

                if (comparison) {

                  rawResults =
                    await searchComparison(
                      query
                    );

                } else {

                  rawResults =
                    await searchNormal(
                      query
                    );
                }

                // ------------------------------------------------
                // NORMALIZE
                // ------------------------------------------------

                const normalized =
                  rawResults
                    .map(item =>
                      normalizeResult(
                        item,
                        item.madhhab_name ||
                        item.category ||
                        ""
                      )
                    )
                    .filter(Boolean);

                // ------------------------------------------------
                // DEDUPLICATE
                // ------------------------------------------------

                const finalResults =
                  deduplicateResults(
                    normalized
                  );

                console.log(
                  `🏁 FINAL TURATH RESULTS = ${finalResults.length}`
                );

                res.writeHead(200);

                res.end(
                  JSON.stringify({
                    success: true,

                    mode:
                      comparison
                        ? "comparison"
                        : "shafii",

                    requested: query,

                    count:
                      finalResults.length,

                    passages:
                      finalResults,
                  })
                );

              } catch (error) {

                console.error(
                  "❌ REQUEST ERROR:"
                );

                console.error(error);

                res.writeHead(500);

                res.end(
                  JSON.stringify({
                    success: false,
                    error:
                      error.message ||
                      String(error),
                  })
                );
              }
            }
          );

          return;

        } catch (error) {

          console.error(error);

          res.writeHead(500);

          res.end(
            JSON.stringify({
              success: false,
              error:
                error.message ||
                String(error),
            })
          );

          return;
        }
      }

      // --------------------------------------------------------
      // GET BOOK
      // --------------------------------------------------------

      if (
        req.method === "GET" &&
        req.url.startsWith("/book/")
      ) {

        try {

          const id =
            req.url
              .split("/book/")[1]
              .split("?")[0];

          const book =
            await getBookInfo(id);

          res.writeHead(200);

          res.end(
            JSON.stringify({
              success: true,
              book,
            })
          );

        } catch (error) {

          res.writeHead(500);

          res.end(
            JSON.stringify({
              success: false,
              error:
                error.message ||
                String(error),
            })
          );
        }

        return;
      }

      // --------------------------------------------------------
      // GET PAGE
      // --------------------------------------------------------

      if (
        req.method === "GET" &&
        req.url.startsWith("/page/")
      ) {

        try {

          const parts =
            req.url
              .split("/page/")[1]
              .split("/");

          const bookId = parts[0];
          const page = Number(parts[1]);

          const result =
            await getPage(
              bookId,
              page
            );

          res.writeHead(200);

          res.end(
            JSON.stringify({
              success: true,
              result,
            })
          );

        } catch (error) {

          res.writeHead(500);

          res.end(
            JSON.stringify({
              success: false,
              error:
                error.message ||
                String(error),
            })
          );
        }

        return;
      }

      // --------------------------------------------------------
      // 404
      // --------------------------------------------------------

      res.writeHead(404);

      res.end(
        JSON.stringify({
          success: false,
          error: "Not found",
        })
      );
    }
  );

// ============================================================
// START SERVER
// ============================================================

server.listen(
  PORT,
  HOST,
  () => {

    console.log("");
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

    console.log("");
  }
);
