import http from "node:http";
import { search, getBookInfo, getPage } from "turath-sdk";

const PORT = Number(process.env.TURATH_PORT || 8765);

const MAX_TURATH_RESULTS = 10;
const SEARCH_PAGES = 3;
const RESULTS_PER_QUERY = 20;

/*
============================================================
CATEGORY ID
============================================================

PENTING:

Isi ID kategori Turath sebenar melalui Render Environment.

Contoh:

TURATH_SHAFII_CATEGORY_ID=...
TURATH_HANAFI_CATEGORY_ID=...
TURATH_MALIKI_CATEGORY_ID=...
TURATH_HANBALI_CATEGORY_ID=...

JANGAN teka nombor ID.

SDK rasmi Turath memang menerima category sebagai nombor.
*/

const CATEGORY_IDS = {
  shafii: toNumber(process.env.TURATH_SHAFII_CATEGORY_ID),
  hanafi: toNumber(process.env.TURATH_HANAFI_CATEGORY_ID),
  maliki: toNumber(process.env.TURATH_MALIKI_CATEGORY_ID),
  hanbali: toNumber(process.env.TURATH_HANBALI_CATEGORY_ID)
};


/* ============================================================
   HTTP
============================================================ */

function sendJson(res, data, status = 200) {

  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*"
  });

  res.end(
    JSON.stringify(data)
  );
}


/* ============================================================
   HELPERS
============================================================ */

function toNumber(value) {

  const n = Number(value);

  return Number.isFinite(n)
    ? n
    : null;
}


function cleanText(value) {

  if (typeof value !== "string") {
    return "";
  }

  return value
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}


function normalizeArabic(text) {

  return String(text || "")
    .normalize("NFKC")
    .replace(
      /[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]/g,
      ""
    )
    .replace(/[إأآٱ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه")
    .replace(/ؤ/g, "و")
    .replace(/ئ/g, "ي")
    .replace(/ـ/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}


/* ============================================================
   CATEGORY NAME
============================================================ */

function categoryName(mode) {

  if (mode === "comparison") {
    return "الفقه الشافعي + الحنفي + المالكي + الحنبلي";
  }

  return "الفقه الشافعي";
}


/* ============================================================
   QUERY MAP BM → ARABIC
============================================================ */

const QUERY_MAP = {

  "mandi":
    [
      "الغسل",
      "الاغتسال",
      "غسل الجنابة",
      "الجنابة"
    ],

  "mandi wajib":
    [
      "الغسل الواجب",
      "غسل الجنابة",
      "الجنابة",
      "الاغتسال"
    ],

  "mandi junub":
    [
      "غسل الجنابة",
      "الجنابة",
      "الغسل"
    ],

  "wuduk":
    [
      "الوضوء",
      "نواقض الوضوء",
      "الطهارة"
    ],

  "wudhu":
    [
      "الوضوء",
      "نواقض الوضوء",
      "الطهارة"
    ],

  "solat":
    [
      "الصلاة",
      "أحكام الصلاة",
      "صفة الصلاة"
    ],

  "sembahyang":
    [
      "الصلاة",
      "أحكام الصلاة"
    ],

  "puasa":
    [
      "الصيام",
      "الصوم",
      "أحكام الصيام"
    ],

  "zakat":
    [
      "الزكاة",
      "أحكام الزكاة"
    ],

  "haji":
    [
      "الحج",
      "أحكام الحج"
    ],

  "umrah":
    [
      "العمرة",
      "أحكام العمرة"
    ],

  "najis":
    [
      "النجاسة",
      "النجس",
      "أحكام النجاسة"
    ],

  "hadas":
    [
      "الحدث",
      "الطهارة",
      "الحدث الأكبر",
      "الحدث الأصغر"
    ],

  "aurat":
    [
      "العورة",
      "ستر العورة",
      "أحكام العورة"
    ],

  "nikah":
    [
      "النكاح",
      "الزواج",
      "أحكام النكاح"
    ],

  "kahwin":
    [
      "النكاح",
      "الزواج",
      "أحكام النكاح"
    ],

  "cerai":
    [
      "الطلاق",
      "أحكام الطلاق"
    ],

  "talak":
    [
      "الطلاق",
      "أحكام الطلاق"
    ],

  "haid":
    [
      "الحيض",
      "أحكام الحيض"
    ],

  "nifas":
    [
      "النفاس",
      "أحكام النفاس"
    ],

  "istihadah":
    [
      "الاستحاضة",
      "أحكام الاستحاضة"
    ],

  "tayammum":
    [
      "التيمم",
      "أحكام التيمم"
    ],

  "azan":
    [
      "الأذان",
      "أحكام الأذان"
    ],

  "iqamah":
    [
      "الإقامة",
      "أحكام الإقامة"
    ],

  "riba":
    [
      "الربا",
      "أحكام الربا"
    ],

  "sedekah":
    [
      "الصدقة",
      "أحكام الصدقة"
    ],

  "wakaf":
    [
      "الوقف",
      "أحكام الوقف"
    ],

  "wasiat":
    [
      "الوصية",
      "أحكام الوصية"
    ],

  "faraid":
    [
      "الفرائض",
      "الميراث",
      "أحكام الميراث"
    ],

  "waris":
    [
      "الميراث",
      "الوارث",
      "أحكام المواريث"
    ],

  "hutang":
    [
      "الدين",
      "الديون",
      "القرض"
    ],

  "jual beli":
    [
      "البيع",
      "الشراء",
      "أحكام البيع"
    ],

  "sentuh perempuan":
    [
      "لمس المرأة",
      "مس المرأة",
      "نقض الوضوء بلمس المرأة"
    ],

  "sentuh isteri":
    [
      "لمس الزوجة",
      "مس الزوجة",
      "نقض الوضوء بلمس الزوجة"
    ],

  "jamak":
    [
      "الجمع بين الصلاتين",
      "جمع الصلاة",
      "الجمع بين الصلوات"
    ],

  "qasar":
    [
      "القصر",
      "قصر الصلاة",
      "صلاة المسافر"
    ],

  "musafir":
    [
      "السفر",
      "المسافر",
      "أحكام السفر"
    ],

  "solat musafir":
    [
      "صلاة المسافر",
      "قصر الصلاة",
      "جمع الصلاة"
    ]
};


/* ============================================================
   ARABIC QUERY GENERATOR
============================================================ */

function arabicQueries(originalQuery) {

  const q =
    String(originalQuery || "")
      .toLowerCase()
      .trim();

  const queries = [];

  /*
  Exact phrase
  */

  if (QUERY_MAP[q]) {

    queries.push(
      ...QUERY_MAP[q]
    );
  }

  /*
  Keyword matching
  */

  for (
    const [keyword, arabic]
    of Object.entries(QUERY_MAP)
  ) {

    if (
      q.includes(keyword)
    ) {

      queries.push(
        ...arabic
      );
    }
  }

  /*
  Arabic question itself
  */

  if (
    /[\u0600-\u06FF]/.test(q)
  ) {

    queries.push(
      originalQuery
    );
  }

  /*
  Fallback BM.
  Turath mungkin tidak sebaik carian Arab,
  tetapi ini masih digunakan sebagai fallback.
  */

  if (
    queries.length === 0
  ) {

    queries.push(
      originalQuery
    );
  }

  return [
    ...new Set(
      queries
        .map(
          x => String(x).trim()
        )
        .filter(Boolean)
    )
  ];
}


/* ============================================================
   COMPARISON DETECTION
============================================================ */

function isMadhhabComparison(query) {

  const original =
    String(query || "")
      .toLowerCase();

  const q =
    normalizeArabic(
      query
    );

  const words = [

    "perbandingan",
    "banding",
    "beza",
    "perbezaan",
    "bandingkan",
    "perbandingan mazhab",
    "antara mazhab",
    "menurut mazhab",
    "mazhab mana",
    "mazhab syafie dan hanafi",
    "mazhab syafii dan hanafi",
    "syafie vs hanafi",
    "syafii vs hanafi",
    "syafie dan maliki",
    "syafie dan hanbali"

  ];

  if (
    words.some(
      word =>
        original.includes(word)
    )
  ) {

    return true;
  }

  /*
  Arabic comparison
  */

  const arabicWords = [

    "مقارنة",
    "الفرق بين",
    "المذاهب",
    "المذهب الشافعي والحنفي",
    "المذهب الشافعي والمالكي",
    "المذهب الشافعي والحنبلي",
    "بين المذاهب"

  ];

  return arabicWords.some(
    word =>
      q.includes(
        normalizeArabic(word)
      )
  );
}


/* ============================================================
   CATEGORY CONFIG
============================================================ */

function getCategoryConfig(mode) {

  if (mode === "comparison") {

    return [

      {
        key: "shafii",
        name: "الفقه الشافعي",
        id: CATEGORY_IDS.shafii
      },

      {
        key: "hanafi",
        name: "الفقه الحنفي",
        id: CATEGORY_IDS.hanafi
      },

      {
        key: "maliki",
        name: "الفقه المالكي",
        id: CATEGORY_IDS.maliki
      },

      {
        key: "hanbali",
        name: "الفقه الحنبلي",
        id: CATEGORY_IDS.hanbali
      }

    ];

  }

  return [

    {
      key: "shafii",
      name: "الفقه الشافعي",
      id: CATEGORY_IDS.shafii
    }

  ];
}


/* ============================================================
   CATEGORY VALIDATION
============================================================ */

function validateCategories(mode) {

  const categories =
    getCategoryConfig(
      mode
    );

  const missing =
    categories.filter(
      item =>
        !item.id
    );

  if (
    missing.length
  ) {

    throw new Error(
      "CATEGORY ID belum lengkap: "
      +
      missing
        .map(
          x => x.name
        )
        .join(", ")
    );
  }

  return categories;
}


/* ============================================================
   NORMALIZE HIT
============================================================ */

function normalizeHit(
  hit,
  category
) {

  const meta =
    hit?.meta || {};

  const bookId =
    toNumber(
      hit?.book_id
    );

  const page =
    toNumber(
      meta?.page
    );

  const pageId =
    toNumber(
      meta?.page_id
    );

  const text =
    cleanText(
      hit?.text
    );

  const snippet =
    cleanText(
      hit?.snip
    );

  return {

    source_type:
      "turath",

    category:
      category?.name
      || null,

    category_key:
      category?.key
      || null,

    category_id:
      category?.id
      || null,

    content:
      text || snippet,

    snippet,

    kitab_name:
      meta?.book_name
      || "Turath",

    author:
      meta?.author_name
      || null,

    book_id:
      bookId,

    page,

    page_id:
      pageId,

    vol:
      meta?.vol
      || null,

    headings:
      Array.isArray(
        meta?.headings
      )
        ? meta.headings
        : [],

    url:
      bookId
        ? `https://app.turath.io/book/${bookId}`
        : null
  };
}


/* ============================================================
   SCORE
============================================================ */

function scoreHit(
  hit,
  query
) {

  const content =
    normalizeArabic(
      hit.content
      || ""
    );

  const snippet =
    normalizeArabic(
      hit.snippet
      || ""
    );

  const q =
    normalizeArabic(
      query
    );

  let score = 0;

  /*
  Exact phrase
  */

  if (
    q
    &&
    content.includes(q)
  ) {

    score += 100;
  }

  /*
  Snippet exact
  */

  if (
    q
    &&
    snippet.includes(q)
  ) {

    score += 40;
  }

  /*
  Word matching
  */

  const words =
    q
      .split(/\s+/)
      .filter(
        word =>
          word.length >= 2
      );

  for (
    const word of words
  ) {

    if (
      content.includes(word)
    ) {

      score += 8;
    }

    if (
      snippet.includes(word)
    ) {

      score += 3;
    }
  }

  /*
  Prefer useful passages
  */

  if (
    content.length >= 200
  ) {

    score += 4;
  }

  if (
    content.length >= 500
  ) {

    score += 3;
  }

  return score;
}


/* ============================================================
   SEARCH CATEGORY
============================================================ */

async function searchCategory(
  query,
  category
) {

  const results = [];

  for (
    let page = 1;
    page <= SEARCH_PAGES;
    page++
  ) {

    try {

      console.log(
        `🔎 TURATH `
        + `${category.name} `
        + `| page=${page} `
        + `| ${query}`
      );

      const result =
        await search(
          query,
          {
            category:
              category.id,

            page
          }
        );

      const hits =
        Array.isArray(
          result?.data
        )
          ? result.data
          : [];

      for (
        const raw of hits
      ) {

        const hit =
          normalizeHit(
            raw,
            category
          );

        if (
          !hit.book_id
          ||
          !hit.content
        ) {

          continue;
        }

        results.push({

          ...hit,

          matched_query:
            query,

          score:
            scoreHit(
              hit,
              query
            )

        });
      }

      /*
      Tidak perlu page seterusnya
      kalau API sudah tidak pulangkan data.
      */

      if (
        hits.length === 0
      ) {

        break;
      }

    } catch (error) {

      console.error(
        `❌ SEARCH ERROR `
        + `${category.name}:`,
        error?.message
        || error
      );

      break;
    }
  }

  return results;
}


/* ============================================================
   DEDUPLICATE
============================================================ */

function deduplicate(
  results
) {

  const map =
    new Map();

  for (
    const item of results
  ) {

    const key =
      [
        item.book_id,
        item.page_id,
        item.content
      ].join("|");

    if (
      !map.has(key)
    ) {

      map.set(
        key,
        item
      );

    } else {

      const old =
        map.get(key);

      if (
        item.score >
        old.score
      ) {

        map.set(
          key,
          item
        );
      }
    }
  }

  return [
    ...map.values()
  ];
}


/* ============================================================
   SELECT TOP 10
============================================================ */

function selectTopResults(
  results,
  limit = MAX_TURATH_RESULTS
) {

  const unique =
    deduplicate(
      results
    );

  unique.sort(
    (a, b) =>
      b.score - a.score
  );

  return unique.slice(
    0,
    limit
  );
}


/* ============================================================
   BALANCED COMPARISON RESULTS
============================================================ */

function selectComparisonResults(
  results,
  limit = MAX_TURATH_RESULTS
) {

  const unique =
    deduplicate(
      results
    );

  /*
  Asingkan mengikut mazhab.
  */

  const groups = {

    shafii: [],
    hanafi: [],
    maliki: [],
    hanbali: []

  };

  for (
    const item of unique
  ) {

    if (
      groups[item.category_key]
    ) {

      groups[
        item.category_key
      ].push(item);
    }
  }

  for (
    const key of Object.keys(groups)
  ) {

    groups[key].sort(
      (a, b) =>
        b.score - a.score
    );
  }

  /*
  Ambil sekurang-kurangnya
  1 daripada setiap mazhab
  jika tersedia.
  */

  const selected = [];

  const categoryOrder = [
    "shafii",
    "hanafi",
    "maliki",
    "hanbali"
  ];

  for (
    const key of categoryOrder
  ) {

    if (
      groups[key].length
      &&
      selected.length < limit
    ) {

      selected.push(
        groups[key].shift()
      );
    }
  }

  /*
  Isi baki berdasarkan score.
  */

  const remaining = [

    ...groups.shafii,
    ...groups.hanafi,
    ...groups.maliki,
    ...groups.hanbali

  ];

  remaining.sort(
    (a, b) =>
      b.score - a.score
  );

  for (
    const item of remaining
  ) {

    if (
      selected.length >= limit
    ) {

      break;
    }

    selected.push(
      item
    );
  }

  /*
  Final sort by score.
  */

  selected.sort(
    (a, b) =>
      b.score - a.score
  );

  return selected.slice(
    0,
    limit
  );
}


/* ============================================================
   SEARCH SHAFII
============================================================ */

async function searchShafii(
  originalQuery
) {

  const queries =
    arabicQueries(
      originalQuery
    );

  console.log("");
  console.log(
    "☪️ MODE: FIQH SYAFIE"
  );

  console.log(
    "🌐 ARABIC QUERIES:",
    queries.join(" | ")
  );

  const categories =
    validateCategories(
      "shafii"
    );

  const candidates = [];

  /*
  Cari setiap query.
  */

  for (
    const query of queries
  ) {

    for (
      const category
      of categories
    ) {

      const hits =
        await searchCategory(
          query,
          category
        );

      candidates.push(
        ...hits
      );
    }

    /*
    Jangan terlalu banyak
    request kalau sudah banyak.
    */

    if (
      candidates.length >= 80
    ) {

      break;
    }
  }

  return selectTopResults(
    candidates,
    MAX_TURATH_RESULTS
  );
}


/* ============================================================
   SEARCH COMPARISON
============================================================ */

async function searchComparison(
  originalQuery
) {

  const queries =
    arabicQueries(
      originalQuery
    );

  console.log("");
  console.log(
    "🌍 MODE: PERBANDINGAN MAZHAB"
  );

  console.log(
    "🌐 ARABIC QUERIES:",
    queries.join(" | ")
  );

  const categories =
    validateCategories(
      "comparison"
    );

  const candidates = [];

  for (
    const query of queries
  ) {

    for (
      const category
      of categories
    ) {

      const hits =
        await searchCategory(
          query,
          category
        );

      candidates.push(
        ...hits
      );
    }

    if (
      candidates.length >= 100
    ) {

      break;
    }
  }

  return selectComparisonResults(
    candidates,
    MAX_TURATH_RESULTS
  );
}


/* ============================================================
   MAIN SEARCH
============================================================ */

async function performSearch(
  originalQuery
) {

  const comparison =
    isMadhhabComparison(
      originalQuery
    );

  console.log("");
  console.log(
    "============================================"
  );

  console.log(
    "🔎 SOALAN:",
    originalQuery
  );

  console.log(
    "⚖️ COMPARISON:",
    comparison
  );

  console.log(
    "============================================"
  );

  let passages = [];

  if (
    comparison
  ) {

    passages =
      await searchComparison(
        originalQuery
      );

  } else {

    passages =
      await searchShafii(
        originalQuery
      );
  }

  return {

    passages,

    comparison,

    queries:
      arabicQueries(
        originalQuery
      )

  };
}


/* ============================================================
   HEALTH
============================================================ */

function categoryStatus() {

  return {

    shafii:
      CATEGORY_IDS.shafii,

    hanafi:
      CATEGORY_IDS.hanafi,

    maliki:
      CATEGORY_IDS.maliki,

    hanbali:
      CATEGORY_IDS.hanbali

  };
}


/* ============================================================
   HTTP SERVER
============================================================ */

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
            `http://127.0.0.1:${PORT}`
          );


        /* ====================================================
           HEALTH
        ==================================================== */

        if (
          url.pathname ===
          "/health"
        ) {

          return sendJson(
            res,
            {

              ok: true,

              service:
                "turath",

              max_results:
                MAX_TURATH_RESULTS,

              categories:
                categoryStatus()

            }
          );
        }


        /* ====================================================
           CATEGORIES
        ==================================================== */

        if (
          url.pathname ===
          "/categories"
        ) {

          return sendJson(
            res,
            {

              ok: true,

              categories: [

                {
                  key:
                    "shafii",

                  name:
                    "الفقه الشافعي",

                  id:
                    CATEGORY_IDS.shafii
                },

                {
                  key:
                    "hanafi",

                  name:
                    "الفقه الحنفي",

                  id:
                    CATEGORY_IDS.hanafi
                },

                {
                  key:
                    "maliki",

                  name:
                    "الفقه المالكي",

                  id:
                    CATEGORY_IDS.maliki
                },

                {
                  key:
                    "hanbali",

                  name:
                    "الفقه الحنبلي",

                  id:
                    CATEGORY_IDS.hanbali
                }

              ]

            }
          );
        }


        /* ====================================================
           SEARCH
        ==================================================== */

        if (
          url.pathname ===
          "/search"
        ) {

          const originalQuery =
            (
              url.searchParams.get(
                "q"
              )
              || ""
            ).trim();

          if (
            !originalQuery
          ) {

            return sendJson(
              res,
              {

                ok: false,

                error:
                  "Parameter q diperlukan"

              },
              400
            );
          }

          const result =
            await performSearch(
              originalQuery
            );

          return sendJson(
            res,
            {

              ok: true,

              query:
                originalQuery,

              mode:
                result.comparison
                  ? "comparison"
                  : "shafii",

              category:
                categoryName(
                  result.comparison
                    ? "comparison"
                    : "shafii"
                ),

              arabic_queries:
                result.queries,

              count:
                result.passages.length,

              passages:
                result.passages

            }
          );
        }


        /* ====================================================
           BOOK INFO
        ==================================================== */

        if (
          url.pathname.startsWith(
            "/book/"
          )
        ) {

          const id =
            toNumber(
              url.pathname
                .split("/")[2]
            );

          if (!id) {

            return sendJson(
              res,
              {

                ok: false,

                error:
                  "Book ID diperlukan"

              },
              400
            );
          }

          const result =
            await getBookInfo(
              id
            );

          return sendJson(
            res,
            {

              ok: true,

              book_id:
                id,

              result

            }
          );
        }


        /* ====================================================
           PAGE
        ==================================================== */

        if (
          url.pathname.startsWith(
            "/page/"
          )
        ) {

          const parts =
            url.pathname.split(
              "/"
            );

          const bookId =
            toNumber(
              parts[2]
            );

          const pageNumber =
            toNumber(
              parts[3]
            );

          if (
            !bookId
            ||
            pageNumber === null
          ) {

            return sendJson(
              res,
              {

                ok: false,

                error:
                  "Book ID dan page diperlukan"

              },
              400
            );
          }

          const result =
            await getPage(
              bookId,
              pageNumber
            );

          return sendJson(
            res,
            {

              ok: true,

              book_id:
                bookId,

              page:
                pageNumber,

              text:
                result?.text
                || "",

              metadata:
                result?.meta
                || null,

              result

            }
          );
        }


        /* ====================================================
           404
        ==================================================== */

        return sendJson(
          res,
          {

            ok: false,

            error:
              "Endpoint tidak dijumpai"

          },
          404
        );

      } catch (error) {

        console.error(
          "❌ TURATH ERROR:",
          error?.message
          || error
        );

        return sendJson(
          res,
          {

            ok: false,

            error:
              error?.message
              || String(error)

          },
          500
        );
      }
    }
  );


/* ============================================================
   START
============================================================ */

server.listen(
  PORT,
  "127.0.0.1",
  () => {

    console.log("");
    console.log(
      "============================================"
    );

    console.log(
      `🚀 TURATH SERVICE`
    );

    console.log(
      `📡 http://127.0.0.1:${PORT}`
    );

    console.log(
      `📚 MAX SOURCES: ${MAX_TURATH_RESULTS}`
    );

    console.log(
      "☪️ SHAFII CATEGORY:",
      CATEGORY_IDS.shafii
    );

    console.log(
      "🕌 HANAFI CATEGORY:",
      CATEGORY_IDS.hanafi
    );

    console.log(
      "🕌 MALIKI CATEGORY:",
      CATEGORY_IDS.maliki
    );

    console.log(
      "🕌 HANBALI CATEGORY:",
      CATEGORY_IDS.hanbali
    );

    console.log(
      "============================================"
    );
  }
);
