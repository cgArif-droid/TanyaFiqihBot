import http from "node:http";
import { search, getBookInfo, getPage } from "turath-sdk";

const PORT = Number(process.env.TURATH_PORT || 8765);
const DEFAULT_LIMIT = Number(process.env.TURATH_SEARCH_K || 3);

const MAX_TURATH_RESULTS = 3;
const MAX_BOOK_DISCOVERY_RESULTS = 20;

const CACHE_TTL_MS = 24 * 60 * 60 * 1000;

let SHAFII_BOOKS_CACHE = null;
let SHAFII_BOOKS_CACHE_TIME = 0;


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


function normalizeTitle(text) {
  return normalizeArabic(
    String(text || "")
      .replace(
        /[\(\)\[\]\{\}:،؛,.]/g,
        " "
      )
  );
}


/* ============================================================
   MAZHAB SYAFIE
   ============================================================ */

const SHAFII_BOOKS = [

  {
    key: "al-muharrar",
    names: [
      "المحرر",
      "المحرر في فقه الشافعي",
      "Al-Muharrar",
      "Muharrar"
    ],
    author: "الرافعي",
    authorAliases: [
      "الرافعي",
      "عبد الكريم الرافعي",
      "Imam al-Rafi'i",
      "Imam al-Rafi"
    ]
  },

  {
    key: "minhaj-al-talibin",
    names: [
      "منهاج الطالبين",
      "منهاج الطالبين وعمدة المفتين",
      "Minhaj al-Talibin",
      "Minhaj al-Talibin wa Umdat al-Muftin"
    ],
    author: "النووي",
    authorAliases: [
      "النووي",
      "يحيى النووي",
      "الإمام النووي",
      "Imam al-Nawawi"
    ]
  },

  {
    key: "rawdat-al-talibin",
    names: [
      "روضة الطالبين",
      "روضة الطالبين وعمدة المفتين",
      "Rawdat al-Talibin"
    ],
    author: "النووي",
    authorAliases: [
      "النووي",
      "الإمام النووي",
      "Imam al-Nawawi"
    ]
  },

  {
    key: "kanz-al-raghibin",
    names: [
      "كنز الراغبين",
      "كنز الراغبين شرح منهاج الطالبين",
      "شرح المحلي على المنهاج",
      "Kanz al-Raghibin"
    ],
    author: "المحلي",
    authorAliases: [
      "المحلي",
      "جلال الدين المحلي",
      "al-Mahalli",
      "Imam al-Mahalli"
    ]
  },

  {
    key: "minhaj-al-tullab",
    names: [
      "منهج الطلاب",
      "منهج الطلاب في فقه الشافعية",
      "Minhaj al-Tullab"
    ],
    author: "زكريا الأنصاري",
    authorAliases: [
      "زكريا الأنصاري",
      "الأنصاري",
      "شيخ الإسلام زكريا الأنصاري",
      "Zakariyya al-Ansari"
    ]
  },

  {
    key: "fath-al-wahhab",
    names: [
      "فتح الوهاب",
      "فتح الوهاب بشرح منهج الطلاب",
      "Fath al-Wahhab"
    ],
    author: "زكريا الأنصاري",
    authorAliases: [
      "زكريا الأنصاري",
      "الأنصاري",
      "Zakariyya al-Ansari"
    ]
  },

  {
    key: "mughni-al-muhtaj",
    names: [
      "مغني المحتاج",
      "مغني المحتاج إلى معرفة معاني ألفاظ المنهاج",
      "Mughni al-Muhtaj"
    ],
    author: "الخطيب الشربيني",
    authorAliases: [
      "الخطيب الشربيني",
      "الشربيني",
      "الخطيب",
      "al-Khatib al-Shirbini"
    ]
  },

  {
    key: "tuhfat-al-muhtaj",
    names: [
      "تحفة المحتاج",
      "تحفة المحتاج في شرح المنهاج",
      "Tuhfat al-Muhtaj"
    ],
    author: "ابن حجر الهيتمي",
    authorAliases: [
      "ابن حجر الهيتمي",
      "ابن حجر",
      "Ibn Hajar al-Haytami"
    ]
  },

  {
    key: "nihayat-al-muhtaj",
    names: [
      "نهاية المحتاج",
      "نهاية المحتاج إلى شرح المنهاج",
      "Nihayat al-Muhtaj"
    ],
    author: "الرملي",
    authorAliases: [
      "الرملي",
      "شمس الدين الرملي",
      "الرملي الكبير",
      "al-Ramli",
      "Imam al-Ramli"
    ]
  },

  {
    key: "asna-al-matalib",
    names: [
      "أسنى المطالب",
      "أسنى المطالب في شرح روض الطالب",
      "Asna al-Matalib"
    ],
    author: "زكريا الأنصاري",
    authorAliases: [
      "زكريا الأنصاري",
      "الأنصاري",
      "Zakariyya al-Ansari"
    ]
  },

  {
    key: "tuhfat-al-muhtaj-hawashi",
    names: [
      "تحفة المحتاج وحواشيه",
      "تحفة المحتاج مع حواشي",
      "حواشي تحفة المحتاج",
      "Tuhfat al-Muhtaj Hawashi"
    ],
    author: "ابن حجر الهيتمي",
    authorAliases: [
      "ابن حجر الهيتمي",
      "ابن حجر",
      "Ibn Hajar al-Haytami"
    ]
  },

  {
    key: "qalyubi-umayra",
    names: [
      "حاشيتا قليوبي وعميرة",
      "حاشية قليوبي وعميرة",
      "حاشيتا قليوبي وعميرة على شرح المحلي",
      "Qalyubi wa Umayrah"
    ],
    author: "القليوبي وعميرة",
    authorAliases: [
      "القليوبي",
      "عميرة",
      "شهاب الدين القليوبي",
      "شهاب الدين عميرة"
    ]
  },

  {
    key: "ianat-al-talibin",
    names: [
      "إعانة الطالبين",
      "إعانة الطالبين على حل ألفاظ فتح المعين",
      "I'anat al-Talibin"
    ],
    author: "البكري الدمياطي",
    authorAliases: [
      "البكري الدمياطي",
      "أبو بكر الدمياطي",
      "الدمياطي",
      "Abu Bakr al-Dimyati"
    ]
  },

  {
    key: "al-aziz",
    names: [
      "العزيز شرح الوجيز",
      "فتح العزيز",
      "العزيز في شرح الوجيز",
      "Fath al-Aziz"
    ],
    author: "الرافعي",
    authorAliases: [
      "الرافعي",
      "Imam al-Rafi'i"
    ]
  },

  {
    key: "al-majmu",
    names: [
      "المجموع",
      "المجموع شرح المهذب",
      "Al-Majmu",
      "Al-Majmu Sharh al-Muhadhdhab"
    ],
    author: "النووي",
    authorAliases: [
      "النووي",
      "الإمام النووي",
      "Imam al-Nawawi"
    ]
  }

];


/* ============================================================
   ARABIC QUERY MAPPING
   ============================================================ */

const QUERY_MAP = {

  "mandi": [
    "الغسل",
    "الاغتسال",
    "غسل الجنابة",
    "الجنابة"
  ],

  "mandi wajib": [
    "الغسل الواجب",
    "غسل الجنابة",
    "الجنابة",
    "الاغتسال"
  ],

  "mandi junub": [
    "غسل الجنابة",
    "الجنابة",
    "الغسل"
  ],

  "mandi janabah": [
    "غسل الجنابة",
    "الجنابة",
    "الغسل"
  ],

  "wuduk": [
    "الوضوء",
    "نواقض الوضوء",
    "الطهارة"
  ],

  "wudhu": [
    "الوضوء",
    "نواقض الوضوء",
    "الطهارة"
  ],

  "solat": [
    "الصلاة",
    "أحكام الصلاة",
    "صفة الصلاة"
  ],

  "sembahyang": [
    "الصلاة",
    "أحكام الصلاة"
  ],

  "puasa": [
    "الصيام",
    "الصوم",
    "أحكام الصيام"
  ],

  "zakat": [
    "الزكاة",
    "أحكام الزكاة"
  ],

  "haji": [
    "الحج",
    "أحكام الحج"
  ],

  "umrah": [
    "العمرة",
    "أحكام العمرة"
  ],

  "najis": [
    "النجاسة",
    "النجس",
    "أحكام النجاسة"
  ],

  "hadas": [
    "الحدث",
    "الطهارة",
    "الحدث الأكبر",
    "الحدث الأصغر"
  ],

  "aurat": [
    "العورة",
    "ستر العورة",
    "أحكام العورة"
  ],

  "nikah": [
    "النكاح",
    "الزواج",
    "أحكام النكاح"
  ],

  "kahwin": [
    "النكاح",
    "الزواج",
    "أحكام النكاح"
  ],

  "cerai": [
    "الطلاق",
    "أحكام الطلاق"
  ],

  "talak": [
    "الطلاق",
    "أحكام الطلاق"
  ],

  "haid": [
    "الحيض",
    "أحكام الحيض"
  ],

  "nifas": [
    "النفاس",
    "أحكام النفاس"
  ],

  "istihadah": [
    "الاستحاضة",
    "أحكام الاستحاضة"
  ],

  "tayammum": [
    "التيمم",
    "أحكام التيمم"
  ],

  "azan": [
    "الأذان",
    "أحكام الأذان"
  ],

  "iqamah": [
    "الإقامة",
    "أحكام الإقامة"
  ],

  "riba": [
    "الربا",
    "أحكام الربا"
  ],

  "sedekah": [
    "الصدقة",
    "أحكام الصدقة"
  ],

  "wakaf": [
    "الوقف",
    "أحكام الوقف"
  ],

  "wasiat": [
    "الوصية",
    "أحكام الوصية"
  ],

  "faraid": [
    "الفرائض",
    "الميراث",
    "أحكام الميراث"
  ],

  "waris": [
    "الميراث",
    "الوارث",
    "أحكام المواريث"
  ],

  "hutang": [
    "الدين",
    "الديون",
    "القرض"
  ],

  "jual beli": [
    "البيع",
    "الشراء",
    "أحكام البيع"
  ]
};


/* ============================================================
   QUERY GENERATOR
   ============================================================ */

function arabicQueries(originalQuery) {

  const q = originalQuery
    .toLowerCase()
    .trim();

  const queries = [];

  if (QUERY_MAP[q]) {

    queries.push(
      ...QUERY_MAP[q]
    );

  } else {

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
  }

  if (
    queries.length === 0
  ) {

    // Untuk soalan Melayu yang tidak
    // mempunyai mapping khusus.
    //
    // Kita masih hantar soalan asal
    // sebagai fallback.
    queries.push(
      originalQuery
    );
  }

  return [
    ...new Set(queries)
  ];
}


/* ============================================================
   COMPARISON DETECTION
   ============================================================ */

function isMadhhabComparison(
  query
) {

  const q = normalizeArabic(
    query
  );

  const comparisonWords = [
    "perbandingan",
    "banding",
    "beza",
    "perbezaan",
    "mazhab",
    "mazahib",
    "syafie dan hanafi",
    "syafii dan hanafi",
    "syafie vs hanafi",
    "syafii vs hanafi",
    "antara mazhab",
    "menurut mazhab"
  ];

  const malay = query
    .toLowerCase();

  return comparisonWords.some(
    word =>
      malay.includes(word)
      ||
      q.includes(
        normalizeArabic(word)
      )
  );
}


/* ============================================================
   BOOK NAME MATCHING
   ============================================================ */

function bookMatchesConfig(
  bookInfo,
  config
) {

  const raw = JSON.stringify(
    bookInfo || {}
  );

  const normalized = normalizeTitle(
    raw
  );

  const nameMatch =
    config.names.some(
      name =>
        normalized.includes(
          normalizeTitle(name)
        )
    );

  const authorMatch =
    config.authorAliases.some(
      author =>
        normalized.includes(
          normalizeTitle(author)
        )
    );

  return nameMatch && authorMatch;
}


/* ============================================================
   DISCOVER BOOK IDS
   ============================================================ */

async function discoverBookIds() {

  const now = Date.now();

  if (
    SHAFII_BOOKS_CACHE
    &&
    now - SHAFII_BOOKS_CACHE_TIME
      < CACHE_TTL_MS
  ) {

    return SHAFII_BOOKS_CACHE;
  }

  console.log("");
  console.log(
    "📚 DISCOVER KITAB SYAFIE"
  );
  console.log(
    "============================================"
  );

  const discovered = [];

  for (
    const config of SHAFII_BOOKS
  ) {

    let found = null;

    for (
      const searchName
      of config.names.slice(0, 4)
    ) {

      try {

        console.log(
          `🔎 CARI KITAB: ${searchName}`
        );

        const result =
          await search(
            searchName
          );

        const hits =
          Array.isArray(
            result?.data
          )
            ? result.data
            : [];

        for (
          const hit of hits.slice(
            0,
            MAX_BOOK_DISCOVERY_RESULTS
          )
        ) {

          const bookId =
            toNumber(
              hit?.book_id
            );

          if (!bookId) {
            continue;
          }

          const meta =
            hit?.meta || {};

          const candidate = {
            book_id: bookId,
            book_name:
              meta?.book_name
              || "",
            author:
              meta?.author_name
              || ""
          };

          const candidateText =
            JSON.stringify(
              candidate
            );

          const normalized =
            normalizeTitle(
              candidateText
            );

          const titleMatch =
            config.names.some(
              name =>
                normalized.includes(
                  normalizeTitle(name)
                )
            );

          const authorMatch =
            config.authorAliases.some(
              author =>
                normalized.includes(
                  normalizeTitle(author)
                )
            );

          if (
            titleMatch
            &&
            authorMatch
          ) {

            found = {
              key: config.key,
              book_id: bookId,
              book_name:
                candidate.book_name,
              author:
                candidate.author
            };

            break;
          }
        }

        if (found) {
          break;
        }

      } catch (error) {

        console.error(
          `❌ DISCOVER ERROR "${searchName}":`,
          error?.message || error
        );
      }
    }

    if (found) {

      console.log(
        `✅ ${config.key} → `
        + `${found.book_id} → `
        + `${found.book_name}`
      );

      discovered.push(
        found
      );

    } else {

      console.log(
        `⚠️ TIDAK JUMPA: `
        + `${config.key}`
      );
    }
  }

  SHAFII_BOOKS_CACHE =
    discovered;

  SHAFII_BOOKS_CACHE_TIME =
    Date.now();

  console.log("");
  console.log(
    `📚 KITAB SYAFIE DIKENALI: `
    + `${discovered.length}`
  );

  return discovered;
}


/* ============================================================
   NORMALIZE HIT
   ============================================================ */

function normalizeHit(hit) {

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
    source_type: "turath",

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

  const text =
    normalizeArabic(
      `${hit.kitab_name || ""} `
      + `${hit.author || ""} `
      + `${hit.content || ""}`
    );

  const q =
    normalizeArabic(
      query
    );

  let score = 0;

  if (
    q
    &&
    text.includes(q)
  ) {

    score += 100;
  }

  const words =
    q
      .split(/\s+/)
      .filter(Boolean);

  for (
    const word of words
  ) {

    if (
      word.length < 2
    ) {
      continue;
    }

    if (
      text.includes(word)
    ) {

      score += 5;
    }
  }

  if (
    hit.content
    &&
    hit.content.length > 100
  ) {

    score += 2;
  }

  return score;
}


/* ============================================================
   SEARCH ONE BOOK
   ============================================================ */

async function searchBook(
  query,
  book
) {

  try {

    console.log(
      `🔎 TURATH BOOK SEARCH: `
      + `${book.book_id} | ${query}`
    );

    const result =
      await search(
        query,
        {
          book: book.book_id
        }
      );

    const hits =
      Array.isArray(
        result?.data
      )
        ? result.data
        : [];

    return hits
      .map(
        normalizeHit
      )
      .filter(
        item =>
          item.content
          &&
          item.book_id
      );

  } catch (error) {

    console.error(
      `❌ BOOK SEARCH ERROR `
      + `${book.book_id}:`,
      error?.message || error
    );

    return [];
  }
}


/* ============================================================
   SEARCH SHAFII
   ============================================================ */

async function searchShafii(
  originalQuery,
  limit
) {

  const books =
    await discoverBookIds();

  const queries =
    arabicQueries(
      originalQuery
    );

  console.log(
    "🌐 ARABIC QUERIES:",
    queries.join(" | ")
  );

  const candidates = [];

  const seen =
    new Set();

  for (
    const query of queries
  ) {

    for (
      const book of books
    ) {

      const hits =
        await searchBook(
          query,
          book
        );

      for (
        const hit of hits
      ) {

        const key =
          [
            hit.book_id,
            hit.page_id,
            hit.content
          ].join("|");

        if (
          seen.has(key)
        ) {
          continue;
        }

        seen.add(key);

        candidates.push({
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
    }

    if (
      candidates.length >=
      limit * 5
    ) {
      break;
    }
  }

  candidates.sort(
    (a, b) =>
      b.score - a.score
  );

  return candidates.slice(
    0,
    limit
  );
}


/* ============================================================
   GENERAL SEARCH
   ============================================================ */

async function searchGeneral(
  originalQuery,
  limit
) {

  const queries =
    arabicQueries(
      originalQuery
    );

  const candidates = [];

  const seen =
    new Set();

  for (
    const query of queries
  ) {

    try {

      console.log(
        `🔎 GENERAL TURATH: `
        + `${query}`
      );

      const result =
        await search(
          query
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
            raw
          );

        if (
          !hit.content
        ) {
          continue;
        }

        const key =
          [
            hit.book_id,
            hit.page_id,
            hit.content
          ].join("|");

        if (
          seen.has(key)
        ) {
          continue;
        }

        seen.add(key);

        candidates.push({
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

    } catch (error) {

      console.error(
        `❌ GENERAL QUERY ERROR `
        + `"${query}":`,
        error?.message || error
      );
    }

    if (
      candidates.length >=
      limit * 5
    ) {
      break;
    }
  }

  candidates.sort(
    (a, b) =>
      b.score - a.score
  );

  return candidates.slice(
    0,
    limit
  );
}


/* ============================================================
   MAIN SEARCH
   ============================================================ */

async function performSearch(
  originalQuery,
  limit
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
    "🔎 TURATH ORIGINAL QUERY:",
    originalQuery
  );
  console.log(
    "⚖️ PERBANDINGAN MAZHAB:",
    comparison
  );
  console.log(
    "============================================"
  );

  let passages;

  if (comparison) {

    console.log(
      "🌍 MODE: PERBANDINGAN MAZHAB"
    );

    passages =
      await searchGeneral(
        originalQuery,
        limit
      );

  } else {

    console.log(
      "☪️ MODE: MAZHAB SYAFIE"
    );

    passages =
      await searchShafii(
        originalQuery,
        limit
      );
  }

  return {
    passages,
    comparison
  };
}


/* ============================================================
   HTTP SERVER
   ============================================================ */

const server =
  http.createServer(
    async (req, res) => {

      try {

        const url =
          new URL(
            req.url,
            `http://127.0.0.1:${PORT}`
          );

        /* ----------------------------------------------------
           HEALTH
           ---------------------------------------------------- */

        if (
          url.pathname ===
          "/health"
        ) {

          return sendJson(
            res,
            {
              ok: true,
              service: "turath",
              shafii_books:
                SHAFII_BOOKS.length
            }
          );
        }


        /* ----------------------------------------------------
           SHAFII BOOKS
           ---------------------------------------------------- */

        if (
          url.pathname ===
          "/shafii-books"
        ) {

          const books =
            await discoverBookIds();

          return sendJson(
            res,
            {
              ok: true,
              count:
                books.length,
              books
            }
          );
        }


        /* ----------------------------------------------------
           SEARCH
           ---------------------------------------------------- */

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

          const limit =
            Math.max(
              1,
              Math.min(
                Number(
                  url.searchParams.get(
                    "limit"
                  )
                  || DEFAULT_LIMIT
                ),
                MAX_TURATH_RESULTS
              )
            );

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
              originalQuery,
              limit
            );

          console.log("");
          console.log(
            "📖 TURATH FINAL:",
            result.passages.length,
            "passages"
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

              count:
                result.passages.length,

              passages:
                result.passages
            }
          );
        }


        /* ----------------------------------------------------
           BOOK INFO
           ---------------------------------------------------- */

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

          console.log(
            "📚 TURATH BOOK:",
            id
          );

          const result =
            await getBookInfo(
              id
            );

          return sendJson(
            res,
            {
              ok: true,
              book_id: id,
              result
            }
          );
        }


        /* ----------------------------------------------------
           PAGE
           ---------------------------------------------------- */

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

          console.log(
            `📖 TURATH PAGE: `
            + `${bookId} / ${pageNumber}`
          );

          const result =
            await getPage(
              bookId,
              pageNumber
            );

          return sendJson(
            res,
            {
              ok: true,
              book_id: bookId,
              page: pageNumber,
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
          error?.message || error
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


server.listen(
  PORT,
  "127.0.0.1",
  () => {

    console.log(
      `🚀 Turath service running `
      + `on http://127.0.0.1:${PORT}`
    );

    console.log(
      `☪️ Syafie whitelist: `
      + `${SHAFII_BOOKS.length} kitab`
    );
  }
);
