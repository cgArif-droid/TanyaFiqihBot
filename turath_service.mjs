import http from "node:http";
import { search, getBookInfo, getPage } from "turath-sdk";

const PORT = 8765;
const HOST = "127.0.0.1";

/*
|--------------------------------------------------------------------------
| CONFIG
|--------------------------------------------------------------------------
*/

const SEARCH_PAGES = 10;
const RESULTS_PER_PAGE = 20;

/*
 * Soalan biasa:
 *   Syafie = 10
 *
 * Perbandingan:
 *   Syafie = 10
 *   Hanafi = 2
 *   Maliki = 2
 *   Hanbali = 2
 */

const NORMAL_LIMITS = {
    shafii: 10
};

const COMPARISON_LIMITS = {
    shafii: 10,
    hanafi: 2,
    maliki: 2,
    hanbali: 2
};


/*
|--------------------------------------------------------------------------
| CATEGORY ID
|--------------------------------------------------------------------------
*/

function toNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
}

const CATEGORY_IDS = {
    hanafi: toNumber(process.env.TURATH_HANAFI_CATEGORY_ID),
    maliki: toNumber(process.env.TURATH_MALIKI_CATEGORY_ID),
    shafii: toNumber(process.env.TURATH_SHAFII_CATEGORY_ID),
    hanbali: toNumber(process.env.TURATH_HANBALI_CATEGORY_ID)
};

const CATEGORY_NAMES = {
    hanafi: "الفقه الحنفي",
    maliki: "الفقه المالكي",
    shafii: "الفقه الشافعي",
    hanbali: "الفقه الحنبلي"
};


/*
|--------------------------------------------------------------------------
| QUERY MAP
|--------------------------------------------------------------------------
*/

const QUERY_MAP = {

    wuduk: [
        "الوضوء",
        "أحكام الوضوء",
        "فرائض الوضوء",
        "نواقض الوضوء"
    ],

    "air sembahyang": [
        "الوضوء",
        "أحكام الوضوء",
        "نواقض الوضوء"
    ],

    solat: [
        "الصلاة",
        "أحكام الصلاة",
        "شروط الصلاة",
        "أركان الصلاة"
    ],

    sembahyang: [
        "الصلاة",
        "أحكام الصلاة",
        "شروط الصلاة",
        "أركان الصلاة"
    ],

    puasa: [
        "الصيام",
        "أحكام الصيام",
        "شروط الصيام",
        "مفسدات الصيام"
    ],

    zakat: [
        "الزكاة",
        "أحكام الزكاة",
        "نصاب الزكاة",
        "مصارف الزكاة"
    ],

    haji: [
        "الحج",
        "أحكام الحج",
        "مناسك الحج"
    ],

    umrah: [
        "العمرة",
        "أحكام العمرة",
        "مناسك العمرة"
    ],

    tayammum: [
        "التيمم",
        "أحكام التيمم",
        "شروط التيمم"
    ],

    najis: [
        "النجاسة",
        "أحكام النجاسة",
        "إزالة النجاسة"
    ],

    bersuci: [
        "الطهارة",
        "أحكام الطهارة"
    ],

    taharah: [
        "الطهارة",
        "أحكام الطهارة"
    ],

    nikah: [
        "النكاح",
        "أحكام النكاح",
        "الزواج"
    ],

    kahwin: [
        "النكاح",
        "أحكام النكاح",
        "الزواج"
    ],

    talak: [
        "الطلاق",
        "أحكام الطلاق"
    ],

    cerai: [
        "الطلاق",
        "أحكام الطلاق"
    ],

    faraid: [
        "الفرائض",
        "الميراث",
        "أحكام المواريث"
    ],

    pusaka: [
        "الميراث",
        "أحكام المواريث"
    ],

    jual: [
        "البيع",
        "أحكام البيع"
    ],

    beli: [
        "البيع",
        "أحكام البيع"
    ],

    riba: [
        "الربا",
        "أحكام الربا"
    ],

    hutang: [
        "الدين",
        "القرض",
        "أحكام الديون"
    ],

    pinjaman: [
        "القرض",
        "أحكام القرض"
    ],

    korban: [
        "الأضحية",
        "أحكام الأضحية"
    ],

    aqiqah: [
        "العقيقة",
        "أحكام العقيقة"
    ],

    qasar: [
        "القصر",
        "قصر الصلاة",
        "صلاة المسافر"
    ],

    jamak: [
        "الجمع",
        "جمع الصلاة",
        "صلاة المسافر"
    ],

    musafir: [
        "السفر",
        "صلاة المسافر",
        "أحكام المسافر"
    ],

    aurat: [
        "العورة",
        "أحكام العورة",
        "ستر العورة"
    ],

    haid: [
        "الحيض",
        "أحكام الحيض"
    ],

    nifas: [
        "النفاس",
        "أحكام النفاس"
    ],

    istihadah: [
        "الاستحاضة",
        "أحكام الاستحاضة"
    ]
};


/*
|--------------------------------------------------------------------------
| Arabic detection
|--------------------------------------------------------------------------
*/

function containsArabic(text) {
    return /[\u0600-\u06FF]/.test(text);
}


/*
|--------------------------------------------------------------------------
| Build Arabic queries
|--------------------------------------------------------------------------
*/

function arabicQueries(originalQuery) {

    const query = String(originalQuery || "").trim();

    if (!query) {
        return [];
    }

    const results = [];

    /*
     * Jika pengguna terus bertanya dalam Arab,
     * gunakan soalan Arab itu sendiri.
     */

    if (containsArabic(query)) {
        results.push(query);
    }

    /*
     * Cari keyword Bahasa Melayu.
     */

    const lower = query.toLowerCase();

    for (const [keyword, arabicTerms] of Object.entries(QUERY_MAP)) {

        if (lower.includes(keyword)) {
            results.push(...arabicTerms);
        }
    }

    /*
     * Kalau tiada mapping,
     * gunakan soalan asal sebagai fallback.
     */

    if (results.length === 0) {
        results.push(query);
    }

    return [...new Set(results)].slice(0, 20);
}


/*
|--------------------------------------------------------------------------
| Detect comparison
|--------------------------------------------------------------------------
*/

function isMadhhabComparison(text) {

    const q = String(text || "").toLowerCase();

    const terms = [
        "banding",
        "bandingkan",
        "perbandingan",
        "perbezaan mazhab",
        "beza mazhab",
        "mazhab mana",
        "empat mazhab",
        "4 mazhab",

        "syafie dan hanafi",
        "syafii dan hanafi",

        "syafie dan maliki",
        "syafii dan maliki",

        "syafie dan hanbali",
        "syafii dan hanbali",

        "hanafi dan maliki",
        "hanafi dan hanbali",
        "maliki dan hanbali"
    ];

    if (terms.some(term => q.includes(term))) {
        return true;
    }

    /*
     * Arabic comparison
     */

    if (
        q.includes("مقارنة") ||
        q.includes("المذاهب") ||
        q.includes("الحنفي") ||
        q.includes("المالكي") ||
        q.includes("الحنبلي")
    ) {
        return true;
    }

    return false;
}


/*
|--------------------------------------------------------------------------
| Validate category
|--------------------------------------------------------------------------
*/

function validateCategory(categoryKey) {

    if (!CATEGORY_IDS[categoryKey]) {

        throw new Error(
            `CATEGORY ID tidak ditetapkan untuk ${categoryKey}`
        );
    }
}


/*
|--------------------------------------------------------------------------
| Normalize result
|--------------------------------------------------------------------------
*/

function normalizeHit(
    hit,
    categoryKey,
    query,
    page
) {

    if (!hit) {
        return null;
    }

    return {

        book_id: hit.book_id,

        author_id: hit.author_id,

        category_id:
            hit.cat_id ||
            CATEGORY_IDS[categoryKey],

        category:
            CATEGORY_NAMES[categoryKey],

        meta:
            hit.meta || "",

        snippet:
            hit.snip || "",

        text:
            hit.text || "",

        search_query:
            query,

        search_page:
            page
    };
}


/*
|--------------------------------------------------------------------------
| Unique key
|--------------------------------------------------------------------------
*/

function resultKey(item) {

    return [
        item.book_id,
        item.meta,
        item.text
    ].join("|");
}


/*
|--------------------------------------------------------------------------
| Search MANY pages
|--------------------------------------------------------------------------
|
| Ini sengaja cari banyak dahulu.
|
*/

async function searchCategoryMany(
    query,
    categoryKey
) {

    validateCategory(categoryKey);

    const results = [];
    const seen = new Set();

    for (
        let page = 1;
        page <= SEARCH_PAGES;
        page++
    ) {

        let response;

        try {

            response = await search(
                query,
                {
                    category:
                        CATEGORY_IDS[categoryKey],

                    page
                }
            );

        } catch (error) {

            console.error(
                `❌ TURATH ERROR`,
                categoryKey,
                `page=${page}`,
                error?.message || error
            );

            continue;
        }

        const data =
            Array.isArray(response?.data)
                ? response.data
                : [];

        console.log(
            `🔎 ${categoryKey} | "${query}" | page=${page} | ${data.length} results`
        );

        if (data.length === 0) {
            break;
        }

        for (const hit of data) {

            const item =
                normalizeHit(
                    hit,
                    categoryKey,
                    query,
                    page
                );

            if (!item) {
                continue;
            }

            const key =
                resultKey(item);

            if (seen.has(key)) {
                continue;
            }

            seen.add(key);

            results.push(item);
        }
    }

    console.log(
        `📚 ${categoryKey} collected = ${results.length}`
    );

    return results;
}


/*
|--------------------------------------------------------------------------
| Search one category using MANY queries
|--------------------------------------------------------------------------
*/

async function collectCategory(
    originalQuery,
    categoryKey
) {

    const queries =
        arabicQueries(originalQuery);

    const all = [];
    const seen = new Set();

    console.log(
        `🔍 ${categoryKey} queries:`,
        queries
    );

    /*
     * Setiap query akan cari banyak page.
     */

    for (const query of queries) {

        const results =
            await searchCategoryMany(
                query,
                categoryKey
            );

        for (const item of results) {

            const key =
                resultKey(item);

            if (seen.has(key)) {
                continue;
            }

            seen.add(key);

            all.push(item);
        }
    }

    console.log(
        `📦 ${categoryKey} TOTAL UNIQUE = ${all.length}`
    );

    return all;
}


/*
|--------------------------------------------------------------------------
| Score result
|--------------------------------------------------------------------------
|
| Kita cuba letakkan hasil yang lebih relevan
| di bahagian atas.
|--------------------------------------------------------------------------
*/

function scoreResult(
    item,
    originalQuery
) {

    let score = 0;

    const query =
        String(originalQuery || "")
            .toLowerCase();

    const text =
        `${item.text} ${item.snippet} ${item.meta}`
            .toLowerCase();

    /*
     * Match Arabic query
     */

    const arabicQs =
        arabicQueries(originalQuery);

    for (const q of arabicQs) {

        const words =
            q
                .split(/\s+/)
                .filter(Boolean);

        for (const word of words) {

            if (
                word.length >= 3 &&
                text.includes(word)
            ) {
                score += 2;
            }
        }
    }

    /*
     * Panjang teks yang munasabah.
     */

    if (item.text.length > 100) {
        score += 1;
    }

    /*
     * Snippet tersedia.
     */

    if (item.snippet) {
        score += 1;
    }

    /*
     * Search page awal biasanya lebih relevan.
     */

    if (item.search_page === 1) {
        score += 2;
    }

    return score;
}


/*
|--------------------------------------------------------------------------
| Rank + limit
|--------------------------------------------------------------------------
*/

function rankAndLimit(
    results,
    originalQuery,
    limit
) {

    const scored =
        results.map(item => ({
            ...item,
            relevance:
                scoreResult(
                    item,
                    originalQuery
                )
        }));

    scored.sort(
        (a, b) =>
            b.relevance -
            a.relevance
    );

    return scored.slice(
        0,
        limit
    );
}


/*
|--------------------------------------------------------------------------
| NORMAL FIQH
|--------------------------------------------------------------------------
|
| Syafie = 10
|--------------------------------------------------------------------------
*/

async function searchNormal(
    originalQuery
) {

    const all =
        await collectCategory(
            originalQuery,
            "shafii"
        );

    const selected =
        rankAndLimit(
            all,
            originalQuery,
            NORMAL_LIMITS.shafii
        );

    console.log(
        `✅ NORMAL FINAL SYAFII = ${selected.length}`
    );

    return selected;
}


/*
|--------------------------------------------------------------------------
| COMPARISON
|--------------------------------------------------------------------------
|
| Syafie = 10
| Hanafi = 2
| Maliki = 2
| Hanbali = 2
|--------------------------------------------------------------------------
*/

async function searchComparison(
    originalQuery
) {

    const categoryResults = {};

    /*
     * Cari semua kategori.
     * Bukan 2 sahaja.
     * Kita cari banyak dahulu.
     */

    for (const categoryKey of [
        "shafii",
        "hanafi",
        "maliki",
        "hanbali"
    ]) {

        categoryResults[categoryKey] =
            await collectCategory(
                originalQuery,
                categoryKey
            );
    }

    /*
     * Kemudian baru pilih jumlah akhir.
     */

    const final = [];

    for (const categoryKey of [
        "shafii",
        "hanafi",
        "maliki",
        "hanbali"
    ]) {

        const limit =
            COMPARISON_LIMITS[
                categoryKey
            ];

        const selected =
            rankAndLimit(
                categoryResults[
                    categoryKey
                ],
                originalQuery,
                limit
            );

        final.push(...selected);

        console.log(
            `✅ ${CATEGORY_NAMES[categoryKey]} FINAL = ${selected.length}`
        );
    }

    return final;
}


/*
|--------------------------------------------------------------------------
| MAIN SEARCH
|--------------------------------------------------------------------------
*/

async function performSearch(
    originalQuery
) {

    const comparison =
        isMadhhabComparison(
            originalQuery
        );

    console.log("");
    console.log(
        "================================================"
    );
    console.log(
        "📚 TURATH SEARCH"
    );
    console.log(
        `❓ ${originalQuery}`
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

    const passages =
        comparison
            ? await searchComparison(
                originalQuery
            )
            : await searchNormal(
                originalQuery
            );

    console.log(
        `🏁 FINAL TURATH RESULTS = ${passages.length}`
    );

    return {

        mode:
            comparison
                ? "comparison"
                : "shafii",

        requested:
            comparison
                ? COMPARISON_LIMITS
                : NORMAL_LIMITS,

        count:
            passages.length,

        passages
    };
}


/*
|--------------------------------------------------------------------------
| JSON
|--------------------------------------------------------------------------
*/

function sendJson(
    res,
    statusCode,
    data
) {

    const body =
        JSON.stringify(data);

    res.writeHead(
        statusCode,
        {
            "Content-Type":
                "application/json; charset=utf-8",

            "Content-Length":
                Buffer.byteLength(body)
        }
    );

    res.end(body);
}


/*
|--------------------------------------------------------------------------
| Read POST body
|--------------------------------------------------------------------------
*/

function readBody(req) {

    return new Promise(
        (resolve, reject) => {

            let body = "";

            req.on(
                "data",
                chunk => {
                    body += chunk;
                }
            );

            req.on(
                "end",
                () => {

                    try {

                        resolve(
                            body
                                ? JSON.parse(body)
                                : {}
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


/*
|--------------------------------------------------------------------------
| HTTP SERVER
|--------------------------------------------------------------------------
*/

const server =
    http.createServer(
        async (req, res) => {

            try {

                /*
                 * HEALTH
                 */

                if (
                    req.method === "GET" &&
                    req.url === "/health"
                ) {

                    return sendJson(
                        res,
                        200,
                        {
                            ok: true,

                            service:
                                "turath",

                            search_pages:
                                SEARCH_PAGES,

                            normal_limits:
                                NORMAL_LIMITS,

                            comparison_limits:
                                COMPARISON_LIMITS,

                            categories:
                                CATEGORY_IDS
                        }
                    );
                }


                /*
                 * CATEGORIES
                 */

                if (
                    req.method === "GET" &&
                    req.url === "/categories"
                ) {

                    return sendJson(
                        res,
                        200,
                        {
                            ids:
                                CATEGORY_IDS,

                            names:
                                CATEGORY_NAMES
                        }
                    );
                }


                /*
                 * SEARCH
                 */

                if (
                    req.method === "POST" &&
                    req.url === "/search"
                ) {

                    const body =
                        await readBody(req);

                    const query =
                        String(
                            body.query || ""
                        ).trim();

                    if (!query) {

                        return sendJson(
                            res,
                            400,
                            {
                                error:
                                    "Query kosong"
                            }
                        );
                    }

                    const result =
                        await performSearch(
                            query
                        );

                    return sendJson(
                        res,
                        200,
                        result
                    );
                }


                /*
                 * BOOK
                 */

                if (
                    req.method === "GET" &&
                    req.url.startsWith(
                        "/book/"
                    )
                ) {

                    const id =
                        Number(
                            req.url
                                .split("/")[2]
                                .split("?")[0]
                        );

                    if (
                        !Number.isFinite(id)
                    ) {

                        return sendJson(
                            res,
                            400,
                            {
                                error:
                                    "Book ID tidak sah"
                            }
                        );
                    }

                    const result =
                        await getBookInfo(
                            id
                        );

                    return sendJson(
                        res,
                        200,
                        result
                    );
                }


                /*
                 * PAGE
                 */

                if (
                    req.method === "GET" &&
                    req.url.startsWith(
                        "/page/"
                    )
                ) {

                    const parts =
                        req.url.split("/");

                    const bookId =
                        Number(parts[2]);

                    const page =
                        Number(
                            parts[3]
                                ?.split("?")[0]
                        );

                    if (
                        !Number.isFinite(bookId) ||
                        !Number.isFinite(page)
                    ) {

                        return sendJson(
                            res,
                            400,
                            {
                                error:
                                    "Book ID/page tidak sah"
                            }
                        );
                    }

                    const result =
                        await getPage(
                            bookId,
                            page
                        );

                    return sendJson(
                        res,
                        200,
                        result
                    );
                }


                /*
                 * NOT FOUND
                 */

                return sendJson(
                    res,
                    404,
                    {
                        error:
                            "Not found"
                    }
                );

            } catch (error) {

                console.error(
                    "❌ TURATH SERVICE ERROR:",
                    error
                );

                return sendJson(
                    res,
                    500,
                    {
                        error:
                            error?.message ||
                            String(error)
                    }
                );
            }
        }
    );


/*
|--------------------------------------------------------------------------
| START
|--------------------------------------------------------------------------
*/

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
            "🔎 SEARCH PAGES:",
            SEARCH_PAGES
        );

        console.log(
            "📖 NORMAL:",
            NORMAL_LIMITS
        );

        console.log(
            "⚖️ COMPARISON:",
            COMPARISON_LIMITS
        );

        console.log(
            "=============================================="
        );
    }
);
