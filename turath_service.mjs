import http from "node:http";
import { search, getBookInfo, getPage } from "turath-sdk";

const PORT = Number(process.env.TURATH_PORT || 8765);
const DEFAULT_LIMIT = Number(process.env.TURATH_SEARCH_K || 8);

function sendJson(res, data, status = 200) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*"
  });

  res.end(JSON.stringify(data));
}

function toNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
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

function normalizeHit(hit) {
  const meta = hit?.meta || {};

  const bookId = toNumber(hit?.book_id);
  const page = toNumber(meta?.page);
  const pageId = toNumber(meta?.page_id);

  const text = cleanText(hit?.text);
  const snippet = cleanText(hit?.snip);

  return {
    source_type: "turath",

    content: text || snippet,

    snippet: snippet,

    kitab_name:
      meta?.book_name ||
      "Turath",

    author:
      meta?.author_name ||
      null,

    book_id:
      bookId,

    page:
      page,

    page_id:
      pageId,

    vol:
      meta?.vol ||
      null,

    headings:
      Array.isArray(meta?.headings)
        ? meta.headings
        : [],

    url:
      bookId
        ? `https://app.turath.io/book/${bookId}`
        : null
  };
}

function normalizeCandidate(hit) {
  const item = normalizeHit(hit);

  return {
    book_id: item.book_id,
    page: item.page,
    page_id: item.page_id,
    kitab_name: item.kitab_name,
    author: item.author,
    vol: item.vol,
    url: item.url
  };
}


/*
============================================================
TERJEMAHAN QUERY BM → ARAB
============================================================
*/

function arabicQueries(query) {

  const q = query
    .toLowerCase()
    .trim();

  const map = {

    "mandi": [
      "غسل",
      "الاغتسال",
      "الغسل",
      "الجنابة",
      "الطهارة"
    ],

    "mandi wajib": [
      "غسل",
      "الغسل",
      "الاغتسال",
      "غسل الجنابة",
      "الجنابة"
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
      "وضوء",
      "الوضوء",
      "الطهارة"
    ],

    "wudhu": [
      "وضوء",
      "الوضوء",
      "الطهارة"
    ],

    "solat": [
      "الصلاة",
      "الصلوة",
      "الصلاة"
    ],

    "sembahyang": [
      "الصلاة",
      "الصلوة"
    ],

    "puasa": [
      "الصيام",
      "الصوم"
    ],

    "zakat": [
      "الزكاة",
      "الزكاة"
    ],

    "haji": [
      "الحج",
      "الحج"
    ],

    "umrah": [
      "العمرة",
      "العمرة"
    ],

    "najis": [
      "النجاسة",
      "النجس"
    ],

    "hadas": [
      "الحدث",
      "الطهارة"
    ],

    "aurat": [
      "العورة",
      "عورة"
    ],

    "nikah": [
      "النكاح",
      "الزواج"
    ],

    "kahwin": [
      "النكاح",
      "الزواج"
    ],

    "cerai": [
      "الطلاق"
    ],

    "talak": [
      "الطلاق"
    ],

    "haid": [
      "الحيض"
    ],

    "period": [
      "الحيض"
    ],

    "nifas": [
      "النفاس"
    ],

    "istihadah": [
      "الاستحاضة"
    ],

    "solat jamak": [
      "الجمع بين الصلاتين",
      "الجمع",
      "صلاة المسافر"
    ],

    "solat qasar": [
      "القصر",
      "صلاة المسافر"
    ],

    "azan": [
      "الأذان"
    ],

    "iqamah": [
      "الإقامة"
    ],

    "tayammum": [
      "التيمم"
    ],

    "wasiat": [
      "الوصية"
    ],

    "faraid": [
      "الفرائض",
      "الميراث"
    ],

    "waris": [
      "الميراث",
      "الوارث"
    ],

    "hutang": [
      "الدين",
      "الديون"
    ],

    "jual beli": [
      "البيع",
      "الشراء"
    ],

    "riba": [
      "الربا"
    ],

    "sedekah": [
      "الصدقة"
    ],

    "wakaf": [
      "الوقف"
    ],

    "quran": [
      "القرآن"
    ],

    "al quran": [
      "القرآن"
    ],

    "hadis": [
      "الحديث"
    ],

    "hadith": [
      "الحديث"
    ]
  };

  if (map[q]) {
    return map[q];
  }

  /*
  Jika ayat panjang,
  cari kata kunci BM yang biasa.
  */

  const results = [];

  const keywordMap = [
    ["mandi", "الغسل"],
    ["junub", "الجنابة"],
    ["janabah", "الجنابة"],
    ["wuduk", "الوضوء"],
    ["wudhu", "الوضوء"],
    ["solat", "الصلاة"],
    ["sembahyang", "الصلاة"],
    ["puasa", "الصيام"],
    ["zakat", "الزكاة"],
    ["haji", "الحج"],
    ["umrah", "العمرة"],
    ["najis", "النجاسة"],
    ["hadas", "الحدث"],
    ["aurat", "العورة"],
    ["nikah", "النكاح"],
    ["kahwin", "الزواج"],
    ["cerai", "الطلاق"],
    ["talak", "الطلاق"],
    ["haid", "الحيض"],
    ["nifas", "النفاس"],
    ["istihadah", "الاستحاضة"],
    ["tayammum", "التيمم"],
    ["azan", "الأذان"],
    ["iqamah", "الإقامة"],
    ["riba", "الربا"],
    ["sedekah", "الصدقة"],
    ["wakaf", "الوقف"],
    ["wasiat", "الوصية"],
    ["faraid", "الفرائض"],
    ["waris", "الميراث"],
    ["hutang", "الدين"],
    ["jual beli", "البيع"],
    ["hadis", "الحديث"],
    ["hadith", "الحديث"],
    ["quran", "القرآن"],
    ["al quran", "القرآن"]
  ];

  for (const [keyword, arabic] of keywordMap) {

    if (q.includes(keyword)) {

      if (!results.includes(arabic)) {
        results.push(arabic);
      }
    }
  }

  /*
  Kalau tak jumpa mapping,
  cuba query asal juga.
  */

  if (results.length === 0) {
    results.push(query);
  }

  return results;
}


/*
============================================================
SEARCH SATU QUERY
============================================================
*/

async function runTurathSearch(query, limit) {

  console.log("🔎 TURATH SEARCH:", query);

  const result = await search(query);

  const hits =
    Array.isArray(result?.data)
      ? result.data
      : [];

  console.log(
    `📊 TURATH "${query}" COUNT: ${result?.count || 0}`
  );

  console.log(
    `📊 TURATH "${query}" DATA: ${hits.length}`
  );

  const passages = hits
    .map(normalizeHit)
    .filter(item => item.content)
    .slice(0, limit);

  const candidates = hits
    .map(normalizeCandidate)
    .filter(item =>
      item.book_id &&
      item.page !== null
    )
    .slice(0, limit * 2);

  return {
    query,
    count: result?.count || hits.length,
    passages,
    candidates
  };
}


/*
============================================================
SERVER
============================================================
*/

const server = http.createServer(async (req, res) => {

  try {

    const url = new URL(
      req.url,
      `http://127.0.0.1:${PORT}`
    );


    /*
    HEALTH
    */

    if (url.pathname === "/health") {

      return sendJson(res, {
        ok: true,
        service: "turath"
      });
    }


    /*
    SEARCH
    */

    if (url.pathname === "/search") {

      const originalQuery =
        (url.searchParams.get("q") || "")
          .trim();

      const limit = Math.max(
        1,
        Math.min(
          Number(
            url.searchParams.get("limit")
            || DEFAULT_LIMIT
          ),
          20
        )
      );


      if (!originalQuery) {

        return sendJson(
          res,
          {
            ok: false,
            error: "Parameter q diperlukan"
          },
          400
        );
      }


      console.log("");
      console.log("============================================");
      console.log(
        "🔎 TURATH ORIGINAL QUERY:",
        originalQuery
      );
      console.log("============================================");


      /*
      Dapatkan query Arab
      */

      const queries =
        arabicQueries(originalQuery);


      console.log(
        "🌐 TURATH QUERIES:",
        queries.join(" | ")
      );


      const allPassages = [];
      const allCandidates = [];

      const seenPassages = new Set();
      const seenCandidates = new Set();


      /*
      Cuba setiap query
      */

      for (const query of queries) {

        try {

          const result =
            await runTurathSearch(
              query,
              limit
            );


          /*
          Simpan passages
          */

          for (const passage of result.passages) {

            const key =
              [
                passage.book_id,
                passage.page,
                passage.content
              ].join("|");


            if (!seenPassages.has(key)) {

              seenPassages.add(key);

              allPassages.push({
                ...passage,
                matched_query: query
              });
            }
          }


          /*
          Simpan candidates
          */

          for (const candidate of result.candidates) {

            const key =
              [
                candidate.book_id,
                candidate.page
              ].join("|");


            if (!seenCandidates.has(key)) {

              seenCandidates.add(key);

              allCandidates.push({
                ...candidate,
                matched_query: query
              });
            }
          }


          /*
          Kalau sudah dapat banyak hasil,
          tak perlu query lagi.
          */

          if (allPassages.length >= limit) {
            break;
          }

        } catch (error) {

          console.error(
            `❌ TURATH QUERY ERROR "${query}":`,
            error?.message || error
          );

        }
      }


      const finalPassages =
        allPassages.slice(0, limit);


      const finalCandidates =
        allCandidates.slice(0, limit * 2);


      console.log("");
      console.log(
        "📖 TURATH DIRECT PASSAGES:",
        finalPassages.length
      );

      console.log(
        "📚 TURATH CANDIDATES:",
        finalCandidates.length
      );

      console.log(
        "✅ TURATH FINAL:",
        finalPassages.length,
        "passages"
      );


      return sendJson(res, {

        ok: true,

        query: originalQuery,

        translated_queries: queries,

        count: finalPassages.length,

        passages: finalPassages,

        candidates: finalCandidates

      });
    }


    /*
    BOOK
    */

    if (url.pathname.startsWith("/book/")) {

      const id =
        toNumber(
          url.pathname.split("/")[2]
        );


      if (!id) {

        return sendJson(
          res,
          {
            ok: false,
            error: "Book ID diperlukan"
          },
          400
        );
      }


      console.log(
        "📚 TURATH BOOK:",
        id
      );


      const result =
        await getBookInfo(id);


      return sendJson(res, {

        ok: true,

        book_id: id,

        result

      });
    }


    /*
    PAGE
    */

    if (url.pathname.startsWith("/page/")) {

      const parts =
        url.pathname.split("/");

      const bookId =
        toNumber(parts[2]);

      const pageNumber =
        toNumber(parts[3]);


      if (
        !bookId ||
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
        `📖 TURATH PAGE: ${bookId} / ${pageNumber}`
      );


      const result =
        await getPage(
          bookId,
          pageNumber
        );


      return sendJson(res, {

        ok: true,

        book_id: bookId,

        page: pageNumber,

        text:
          result?.text || "",

        metadata:
          result?.meta || null,

        result

      });
    }


    /*
    404
    */

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
          error?.message ||
          String(error)
      },
      500
    );
  }

});


server.listen(
  PORT,
  "127.0.0.1",
  () => {

    console.log(
      `🚀 Turath service running on http://127.0.0.1:${PORT}`
    );

  }
);
