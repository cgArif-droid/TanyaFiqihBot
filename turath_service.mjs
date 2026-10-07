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

// Berapa banyak maklumat kitab boleh dipanggil serentak
const BOOK_INFO_CONCURRENCY = Number(
  process.env.TURATH_BOOK_INFO_CONCURRENCY || 3
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
// BOOK INFO CACHE
// ============================================================

const bookInfoCache = new Map();

// ============================================================
// GENERIC VALUE HELPERS
// ============================================================

function firstValue(...values) {

  for (const value of values) {

    if (
      value === undefined ||
      value === null
    ) {
      continue;
    }

    if (
      typeof value === "string" &&
      value.trim() === ""
    ) {
      continue;
    }

    return value;
  }

  return "";
}

// ============================================================
// STRING VALUE
// ============================================================

function stringValue(...values) {

  const value =
    firstValue(...values);

  if (
    value === undefined ||
    value === null
  ) {
    return "";
  }

  if (
    typeof value === "object"
  ) {

    try {
      return JSON.stringify(value);
    } catch {
      return "";
    }
  }

  return String(value).trim();
}

// ============================================================
// EXTRACT TEXT
// ============================================================

function extractText(item) {

  if (!item) {
    return "";
  }

  return stringValue(
    item.text,
    item.content,
    item.passage,
    item.snippet,
    item.snip,
    item.highlight,
    item.body,
    item.textContent
  );
}

// ============================================================
// EXTRACT BOOK ID
// ============================================================

function extractBookId(item) {

  if (!item) {
    return "";
  }

  const direct = firstValue(
    item.book_id,
    item.bookId,
    item.bookID,
    item.bookid,
    item.volume_id,
    item.volumeId
  );

  if (
    direct !== undefined &&
    direct !== null &&
    String(direct).trim() !== ""
  ) {
    return String(direct).trim();
  }

  // ----------------------------------------------------------
  // Nested book object
  // ----------------------------------------------------------

  if (
    item.book &&
    typeof item.book === "object"
  ) {

    const nested =
      firstValue(
        item.book.id,
        item.book.book_id,
        item.book.bookId
      );

    if (
      nested !== undefined &&
      nested !== null &&
      String(nested).trim() !== ""
    ) {
      return String(nested).trim();
    }
  }

  // ----------------------------------------------------------
  // Nested metadata
  // ----------------------------------------------------------

  if (
    item.metadata &&
    typeof item.metadata === "object"
  ) {

    const metadata =
      firstValue(
        item.metadata.book_id,
        item.metadata.bookId,
        item.metadata.volume_id,
        item.metadata.volumeId
      );

    if (
      metadata !== undefined &&
      metadata !== null &&
      String(metadata).trim() !== ""
    ) {
      return String(metadata).trim();
    }
  }

  return "";
}

// ============================================================
// EXTRACT PAGE
// ============================================================

function extractPage(item) {

  if (!item) {
    return "";
  }

  const page =
    firstValue(
      item.page,
      item.page_number,
      item.pageNumber,
      item.pageno,
      item.pageNo,
      item.pagination
    );

  if (
    page !== undefined &&
    page !== null &&
    String(page).trim() !== ""
  ) {
    return String(page).trim();
  }

  // ----------------------------------------------------------
  // Nested page
  // ----------------------------------------------------------

  if (
    item.metadata &&
    typeof item.metadata === "object"
  ) {

    const metadataPage =
      firstValue(
        item.metadata.page,
        item.metadata.page_number,
        item.metadata.pageNumber
      );

    if (
      metadataPage !== undefined &&
      metadataPage !== null &&
      String(metadataPage).trim() !== ""
    ) {
      return String(metadataPage).trim();
    }
  }

  return "";
}

// ============================================================
// EXTRACT BOOK TITLE
// ============================================================

function extractBookTitle(item) {

  if (!item) {
    return "";
  }

  const title =
    firstValue(
      item.book_title,
      item.bookTitle,
      item.book_name,
      item.bookName,
      item.title,
      item.source
    );

  if (
    title &&
    typeof title !== "object"
  ) {
    return String(title).trim();
  }

  // ----------------------------------------------------------
  // Nested book object
  // ----------------------------------------------------------

  if (
    item.book &&
    typeof item.book === "object"
  ) {

    const nested =
      firstValue(
        item.book.title,
        item.book.name,
        item.book.book_title,
        item.book.book_name
      );

    if (
      nested &&
      typeof nested !== "object"
    ) {
      return String(nested).trim();
    }
  }

  // ----------------------------------------------------------
  // Metadata
  // ----------------------------------------------------------

  if (
    item.metadata &&
    typeof item.metadata === "object"
  ) {

    const metadataTitle =
      firstValue(
        item.metadata.book_title,
        item.metadata.bookTitle,
        item.metadata.book_name,
        item.metadata.bookName,
        item.metadata.title
      );

    if (
      metadataTitle &&
      typeof metadataTitle !== "object"
    ) {
      return String(metadataTitle).trim();
    }
  }

  return "";
}

// ============================================================
// EXTRACT AUTHOR
// ============================================================

function extractAuthor(item) {

  if (!item) {
    return "";
  }

  const author =
    firstValue(
      item.author,
      item.book_author,
      item.bookAuthor,
      item.author_name,
      item.authorName
    );

  if (
    author &&
    typeof author !== "object"
  ) {
    return String(author).trim();
  }

  // ----------------------------------------------------------
  // Nested book
  // ----------------------------------------------------------

  if (
    item.book &&
    typeof item.book === "object"
  ) {

    const nested =
      firstValue(
        item.book.author,
        item.book.book_author,
        item.book.author_name,
        item.book.authorName
      );

    if (
      nested &&
      typeof nested !== "object"
    ) {
      return String(nested).trim();
    }
  }

  // ----------------------------------------------------------
  // Metadata
  // ----------------------------------------------------------

  if (
    item.metadata &&
    typeof item.metadata === "object"
  ) {

    const metadataAuthor =
      firstValue(
        item.metadata.author,
        item.metadata.book_author,
        item.metadata.author_name,
        item.metadata.authorName
      );

    if (
      metadataAuthor &&
      typeof metadataAuthor !== "object"
    ) {
      return String(metadataAuthor).trim();
    }
  }

  return "";
}

// ============================================================
// EXTRACT URL
// ============================================================

function extractUrl(item) {

  if (!item) {
    return "";
  }

  const url =
    firstValue(
      item.url,
      item.link,
      item.href
    );

  if (
    url &&
    typeof url !== "object"
  ) {
    return String(url).trim();
  }

  if (
    item.metadata &&
    typeof item.metadata === "object"
  ) {

    const metadataUrl =
      firstValue(
        item.metadata.url,
        item.metadata.link,
        item.metadata.href
      );

    if (
      metadataUrl &&
      typeof metadataUrl !== "object"
    ) {
      return String(metadataUrl).trim();
    }
  }

  return "";
}

// ============================================================
// EXTRACT RESULT ID
// ============================================================

function extractId(item) {

  if (!item) {
    return "";
  }

  return stringValue(
    item.id,
    item.chunk_id,
    item.chunkId,
    item.result_id,
    item.resultId
  );
}

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
    "مقارنة المذاهب",
    "المذاهب الأربعة",
    "الفرق بين المذاهب",
  ];

  if (
    explicit.some(
      phrase => q.includes(phrase)
    )
  ) {
    return true;
  }

  const madhhabNames = [
    [
      "syafie",
      "syafii",
      "syafi'i",
      "shafii",
      "shafi'i",
      "شافعي",
      "الشافعية",
    ],

    [
      "hanafi",
      "حنفي",
      "الحنفية",
    ],

    [
      "maliki",
      "مالكي",
      "المالكية",
    ],

    [
      "hanbali",
      "حنبلي",
      "الحنابلة",
    ],
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

  if (original) {
    queries.push(original);
  }

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

    const response =
      await search(
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

    // --------------------------------------------------------
    // Debug first result supaya kita tahu struktur SDK sebenar
    // --------------------------------------------------------

    if (
      results.length > 0 &&
      page === 1
    ) {

      console.log(
        "🧪 FIRST RAW TURATH RESULT:"
      );

      console.log(
        JSON.stringify(
          results[0],
          null,
          2
        ).slice(0, 12000)
      );
    }

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

          _query:
            query,

          _categoryId:
            categoryId,
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
    `📦 shafii TOTAL = ${results.length}`
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
// FETCH BOOK INFO
// ============================================================

async function fetchBookInfo(
  bookId
) {

  if (!bookId) {
    return null;
  }

  const key =
    String(bookId);

  // ----------------------------------------------------------
  // CACHE
  // ----------------------------------------------------------

  if (
    bookInfoCache.has(key)
  ) {

    return bookInfoCache.get(key);
  }

  try {

    console.log(
      `📖 GET BOOK INFO: ${key}`
    );

    const info =
      await getBookInfo(key);

    // Simpan walaupun kosong
    bookInfoCache.set(
      key,
      info || null
    );

    console.log(
      `✅ BOOK INFO LOADED: ${key}`
    );

    return info || null;

  } catch (error) {

    console.error(
      `⚠️ GET BOOK INFO FAILED: ${key}`
    );

    console.error(
      error?.message ||
      error
    );

    bookInfoCache.set(
      key,
      null
    );

    return null;
  }
}

// ============================================================
// ENRICH BOOK METADATA
// ============================================================

async function enrichBookMetadata(
  items
) {

  if (!items.length) {
    return items;
  }

  // ----------------------------------------------------------
  // Cari semua book ID
  // ----------------------------------------------------------

  const bookIds = [];

  for (
    const item of items
  ) {

    const bookId =
      extractBookId(item);

    if (
      bookId &&
      !bookIds.includes(bookId)
    ) {

      bookIds.push(bookId);
    }
  }

  if (!bookIds.length) {

    console.log(
      "⚠️ NO BOOK IDS FOUND IN SEARCH RESULTS"
    );

    return items;
  }

  console.log(
    `📚 UNIQUE BOOK IDS = ${bookIds.length}`
  );

  // ----------------------------------------------------------
  // Fetch book info secara terkawal
  // ----------------------------------------------------------

  const infoMap = new Map();

  for (
    let i = 0;
    i < bookIds.length;
    i += BOOK_INFO_CONCURRENCY
  ) {

    const batch =
      bookIds.slice(
        i,
        i + BOOK_INFO_CONCURRENCY
      );

    const results =
      await Promise.all(
        batch.map(
          async bookId => {

            const info =
              await fetchBookInfo(
                bookId
              );

            return [
              bookId,
              info,
            ];
          }
        )
      );

    for (
      const [bookId, info]
      of results
    ) {

      infoMap.set(
        bookId,
        info
      );
    }
  }

  // ----------------------------------------------------------
  // Gabungkan metadata
  // ----------------------------------------------------------

  return items.map(
    item => {

      const bookId =
        extractBookId(item);

      const bookInfo =
        infoMap.get(bookId);

      return {
        ...item,

        _bookInfo:
          bookInfo || null,
      };
    }
  );
}

// ============================================================
// NORMALIZE BOOK INFO
// ============================================================

function normalizeBookInfo(
  bookInfo
) {

  if (!bookInfo) {
    return {};
  }

  // ----------------------------------------------------------
  // Kalau info ada nested "book"
  // ----------------------------------------------------------

  let source =
    bookInfo;

  if (
    bookInfo.book &&
    typeof bookInfo.book === "object"
  ) {

    source =
      {
        ...bookInfo,
        ...bookInfo.book,
      };
  }

  return {
    book:
      stringValue(
        source.title,
        source.name,
        source.book_title,
        source.bookTitle,
        source.book_name,
        source.bookName
      ),

    author:
      stringValue(
        source.author,
        source.author_name,
        source.authorName,
        source.book_author,
        source.bookAuthor
      ),

    book_id:
      stringValue(
        source.id,
        source.book_id,
        source.bookId
      ),

    url:
      stringValue(
        source.url,
        source.link,
        source.href
      ),
  };
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

  const bookInfo =
    normalizeBookInfo(
      item._bookInfo
    );

  const text =
    extractText(item);

  if (!text) {
    return null;
  }

  const bookId =
    firstValue(
      extractBookId(item),
      bookInfo.book_id
    );

  const book =
    firstValue(
      extractBookTitle(item),
      bookInfo.book
    );

  const author =
    firstValue(
      extractAuthor(item),
      bookInfo.author
    );

  const page =
    extractPage(item);

  const url =
    firstValue(
      extractUrl(item),
      bookInfo.url
    );

  const category =
    firstValue(
      item.category,
      item.category_name,
      item.mazhab,
      item.madhhab_name,
      fallbackCategory
    );

  return {

    // --------------------------------------------------------
    // Content
    // --------------------------------------------------------

    text,

    content:
      text,

    // --------------------------------------------------------
    // Book
    // --------------------------------------------------------

    book:
      String(book || "").trim(),

    book_title:
      String(book || "").trim(),

    source:
      String(book || "").trim(),

    author:
      String(author || "").trim(),

    // --------------------------------------------------------
    // Page
    // --------------------------------------------------------

    page:
      String(page || "").trim(),

    page_number:
      String(page || "").trim(),

    // --------------------------------------------------------
    // Category / Madhhab
    // --------------------------------------------------------

    category:
      String(category || "").trim(),

    // --------------------------------------------------------
    // IDs
    // --------------------------------------------------------

    book_id:
      String(bookId || "").trim(),

    id:
      extractId(item),

    // --------------------------------------------------------
    // URL
    // --------------------------------------------------------

    url:
      String(url || "").trim(),

    // --------------------------------------------------------
    // Search info
    // --------------------------------------------------------

    query:
      item._query || "",

    category_id:
      item._categoryId || "",
  };
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
        ""
      ).trim();

    const book =
      String(
        item.book ||
        item.book_title ||
        item.source ||
        ""
      ).trim();

    const author =
      String(
        item.author ||
        ""
      ).trim();

    const page =
      String(
        item.page ||
        item.page_number ||
        ""
      ).trim();

    const bookId =
      String(
        item.book_id ||
        ""
      ).trim();

    const key =
      `${bookId}|${book}|${author}|${page}|${text.slice(0, 300)}`;

    if (
      seen.has(key)
    ) {
      continue;
    }

    seen.add(key);

    unique.push(item);
  }

  return unique;
}

// ============================================================
// ENRICH + NORMALIZE + DEDUPLICATE
// ============================================================

async function prepareResults(
  rawResults
) {

  if (!rawResults.length) {
    return [];
  }

  console.log(
    `🔧 PREPARING ${rawResults.length} RAW RESULTS`
  );

  // ----------------------------------------------------------
  // Ambil metadata kitab
  // ----------------------------------------------------------

  const enriched =
    await enrichBookMetadata(
      rawResults
    );

  // ----------------------------------------------------------
  // Normalize
  // ----------------------------------------------------------

  const normalized =
    enriched
      .map(
        item =>
          normalizeResult(
            item,
            item.madhhab_name ||
            item.category ||
            ""
          )
      )
      .filter(Boolean);

  // ----------------------------------------------------------
  // Deduplicate
  // ----------------------------------------------------------

  const unique =
    deduplicateResults(
      normalized
    );

  console.log(
    `🏁 FINAL NORMALIZED RESULTS = ${unique.length}`
  );

  // ----------------------------------------------------------
  // Debug metadata
  // ----------------------------------------------------------

  if (unique.length > 0) {

    console.log(
      "📚 FIRST NORMALIZED RESULT:"
    );

    console.log(
      JSON.stringify(
        unique[0],
        null,
        2
      ).slice(0, 12000)
    );
  }

  return unique;
}

// ============================================================
// READ REQUEST BODY
// ============================================================

function readRequestBody(
  req
) {

  return new Promise(
    (resolve, reject) => {

      let body = "";

      req.on(
        "data",
        chunk => {
          body += chunk.toString();
        }
      );

      req.on(
        "end",
        () => {

          try {

            resolve(
              JSON.parse(
                body || "{}"
              )
            );

          } catch (error) {

            reject(error);
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
        "Access-Control-Allow-Methods",
        "GET,POST,OPTIONS"
      );

      res.setHeader(
        "Content-Type",
        "application/json; charset=utf-8"
      );

      // --------------------------------------------------------
      // OPTIONS
      // --------------------------------------------------------

      if (
        req.method === "OPTIONS"
      ) {

        res.writeHead(204);

        res.end();

        return;
      }

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
            service:
              "Turath Search Service",
            status:
              "running",
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
            categories:
              CATEGORY_IDS,
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
            names:
              CATEGORY_NAMES,
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

          const payload =
            await readRequestBody(
              req
            );

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

          console.log(
            "================================================"
          );

          console.log(
            "📚 TURATH SEARCH"
          );

          console.log(
            `❓ ${query}`
          );

          console.log(
            `🧭 MODE = ${
              comparison
                ? "COMPARISON"
                : "SHAFII"
            }`
          );

          console.log(
            "================================================"
          );

          if (!query) {

            res.writeHead(400);

            res.end(
              JSON.stringify({
                success: false,
                error:
                  "Query kosong",
              })
            );

            return;
          }

          let rawResults = [];

          // ----------------------------------------------------
          // SEARCH
          // ----------------------------------------------------

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

          console.log(
            `📦 RAW RESULTS = ${rawResults.length}`
          );

          // ----------------------------------------------------
          // PREPARE
          // ----------------------------------------------------

          const finalResults =
            await prepareResults(
              rawResults
            );

          // ----------------------------------------------------
          // RESPONSE
          // ----------------------------------------------------

          res.writeHead(200);

          res.end(
            JSON.stringify({
              success: true,

              mode:
                comparison
                  ? "comparison"
                  : "shafii",

              requested:
                query,

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

          console.error(
            error
          );

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

          if (!id) {

            res.writeHead(400);

            res.end(
              JSON.stringify({
                success: false,
                error:
                  "Book ID kosong",
              })
            );

            return;
          }

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

          console.error(
            "❌ GET BOOK ERROR:"
          );

          console.error(
            error
          );

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

          const bookId =
            parts[0];

          const page =
            Number(parts[1]);

          if (
            !bookId ||
            !Number.isFinite(page)
          ) {

            res.writeHead(400);

            res.end(
              JSON.stringify({
                success: false,
                error:
                  "Book ID atau page tidak sah",
              })
            );

            return;
          }

          const result =
            await getPage(
              bookId,
              page
            );

          res.writeHead(200);

          res.end(
            JSON.stringify({
              success: true,

              book_id:
                bookId,

              page,

              result,
            })
          );

        } catch (error) {

          console.error(
            "❌ GET PAGE ERROR:"
          );

          console.error(
            error
          );

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
          error:
            "Not found",
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
      `📄 SEARCH PAGES: ${SEARCH_PAGES}`
    );

    console.log(
      `🔎 RESULTS/PAGE: ${RESULTS_PER_PAGE}`
    );

    console.log(
      `📖 BOOK INFO CONCURRENCY: ${BOOK_INFO_CONCURRENCY}`
    );

    console.log(
      "=============================================="
    );

    console.log("");
  }
);
