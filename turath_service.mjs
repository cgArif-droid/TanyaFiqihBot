import http from "node:http";
import {
    search,
    getBookInfo,
    getPage
} from "turath-sdk";


/*
|--------------------------------------------------------------------------
| SERVER CONFIG
|--------------------------------------------------------------------------
*/

const PORT = Number(
    process.env.PORT || 8765
);

/*
 * PENTING:
 *
 * Jika Python app.py dan service Turath berada
 * DALAM CONTAINER YANG SAMA:
 *
 *     127.0.0.1
 *
 * boleh digunakan.
 *
 * Jika Turath service berada pada Render Web Service
 * yang BERASINGAN daripada app.py, gunakan:
 *
 *     HOST = "0.0.0.0"
 *
 * dan app.py perlu menggunakan URL service tersebut.
 */

const HOST =
    process.env.HOST ||
    "0.0.0.0";


/*
|--------------------------------------------------------------------------
| SEARCH CONFIG
|--------------------------------------------------------------------------
*/

const SEARCH_PAGES = Number(
    process.env.TURATH_SEARCH_PAGES || 10
);

const RESULTS_PER_PAGE = Number(
    process.env.TURATH_RESULTS_PER_PAGE || 20
);


/*
|--------------------------------------------------------------------------
| FINAL RESULT LIMIT
|--------------------------------------------------------------------------
|
| Soalan biasa:
|
|   Syafie = 10
|
| Perbandingan:
|
|   Syafie = 10
|   Hanafi = 2
|   Maliki = 2
|   Hanbali = 2
|
|--------------------------------------------------------------------------
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

    if (
        value === undefined ||
        value === null ||
        value === ""
    ) {
        return null;
    }

    const n = Number(value);

    return Number.isFinite(n)
        ? n
        : null;
}


const CATEGORY_IDS = {

    hanafi:
        toNumber(
            process.env.TURATH_HANAFI_CATEGORY_ID
        ),

    maliki:
        toNumber(
            process.env.TURATH_MALIKI_CATEGORY_ID
        ),

    shafii:
        toNumber(
            process.env.TURATH_SHAFII_CATEGORY_ID
        ),

    hanbali:
        toNumber(
            process.env.TURATH_HANBALI_CATEGORY_ID
        )
};


const CATEGORY_NAMES = {

    hanafi:
        "الفقه الحنفي",

    maliki:
        "الفقه المالكي",

    shafii:
        "الفقه الشافعي",

    hanbali:
        "الفقه الحنبلي"
};


/*
|--------------------------------------------------------------------------
| PRINT CATEGORY CONFIG
|--------------------------------------------------------------------------
*/

console.log(
    "📚 CATEGORY IDS:",
    CATEGORY_IDS
);


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
| UTILITY
|--------------------------------------------------------------------------
*/

function safeString(value) {

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

            return JSON.stringify(
                value,
                null,
                0
            );

        } catch {

            return String(value);
        }
    }

    return String(value);
}


function containsArabic(text) {

    return /[\u0600-\u06FF]/.test(
        String(text || "")
    );
}


function uniqueArray(array) {

    return [
        ...new Set(
            array.filter(Boolean)
        )
    ];
}


/*
|--------------------------------------------------------------------------
| ARABIC QUERIES
|--------------------------------------------------------------------------
*/

function arabicQueries(
    originalQuery
) {

    const query =
        String(
            originalQuery || ""
        ).trim();

    if (!query) {
        return [];
    }

    const results = [];

    /*
     * Jika pengguna bertanya dalam Arab,
     * masukkan soalan asal.
     */

    if (
        containsArabic(query)
    ) {

        results.push(
            query
        );
    }

    /*
     * Mapping Bahasa Melayu → Arab.
     */

    const lower =
        query.toLowerCase();

    for (
        const [
            keyword,
            arabicTerms
        ]
        of Object.entries(
            QUERY_MAP
        )
    ) {

        if (
            lower.includes(
                keyword
            )
        ) {

            results.push(
                ...arabicTerms
            );
        }
    }

    /*
     * Jika tiada mapping,
     * cuba query asal.
     */

    if (
        results.length === 0
    ) {

        results.push(
            query
        );
    }

    return uniqueArray(
        results
    ).slice(0, 20);
}


/*
|--------------------------------------------------------------------------
| MADHHAB COMPARISON
|--------------------------------------------------------------------------
*/

function isMadhhabComparison(
    text
) {

    const q =
        String(
            text || ""
        ).toLowerCase();

    const terms = [

        "banding",
        "bandingkan",
        "perbandingan",
        "perbezaan mazhab",
        "perbezaan antara mazhab",
        "beza mazhab",
        "mazhab mana",
        "empat mazhab",
        "4 mazhab",
        "semua mazhab",
        "keempat-empat mazhab",

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

    if (
        terms.some(
            term =>
                q.includes(term)
        )
    ) {

        return true;
    }


    /*
     * Explicit 2+ mazhab.
     */

    const madhhabs = [

        "syafie",
        "syafii",
        "hanafi",
        "maliki",
        "hanbali"
    ];

    let count = 0;

    for (
        const madhhab
        of madhhabs
    ) {

        if (
            q.includes(
                madhhab
            )
        ) {

            count++;
        }
    }

    if (
        count >= 2
    ) {

        return true;
    }


    /*
     * Arabic.
     */

    if (

        q.includes("مقارنة") ||
        q.includes("المذاهب") ||
        q.includes("الحنفي") ||
        q.includes("المالكي") ||
        q.includes("الشافعي") ||
        q.includes("الحنبلي")

    ) {

        /*
         * Jangan anggap semua soalan yang hanya
         * menyebut satu mazhab sebagai comparison.
         */

        const arabicMadhhabs = [

            "الحنفي",
            "المالكي",
            "الشافعي",
            "الحنبلي"
        ];

        const arabicCount =
            arabicMadhhabs.filter(
                term =>
                    q.includes(term)
            ).length;

        if (
            q.includes("مقارنة") ||
            q.includes("المذاهب") ||
            arabicCount >= 2
        ) {

            return true;
        }
    }

    return false;
}


/*
|--------------------------------------------------------------------------
| CATEGORY VALIDATION
|--------------------------------------------------------------------------
*/

function validateCategory(
    categoryKey
) {

    const categoryId =
        CATEGORY_IDS[
            categoryKey
        ];

    if (
        !Number.isFinite(
            categoryId
        )
    ) {

        throw new Error(
            `CATEGORY ID tidak ditetapkan untuk ${categoryKey}. ` +
            `Sila tetapkan TURATH_${categoryKey.toUpperCase()}_CATEGORY_ID.`
        );
    }

    return categoryId;
}


/*
|--------------------------------------------------------------------------
| EXTRACT FIELD
|--------------------------------------------------------------------------
|
| SDK mungkin menggunakan nama field yang sedikit berbeza.
| Kita cuba beberapa kemungkinan.
|--------------------------------------------------------------------------
*/

function firstValue(
    object,
    keys
) {

    for (
        const key of keys
    ) {

        const value =
            object?.[key];

        if (
            value !== undefined &&
            value !== null &&
            value !== ""
        ) {

            return value;
        }
    }

    return "";
}


/*
|--------------------------------------------------------------------------
| NORMALIZE TURATH HIT
|--------------------------------------------------------------------------
*/

function normalizeHit(
    hit,
    categoryKey,
    query,
    page
) {

    if (
        !hit ||
        typeof hit !== "object"
    ) {

        return null;
    }


    /*
     * BOOK ID
     */

    const bookId =
        firstValue(
            hit,
            [
                "book_id",
                "bookId",
                "id"
            ]
        );


    /*
     * AUTHOR ID
     */

    const authorId =
        firstValue(
            hit,
            [
                "author_id",
                "authorId"
            ]
        );


    /*
     * CATEGORY
     */

    const categoryId =
        firstValue(
            hit,
            [
                "cat_id",
                "category_id",
                "categoryId"
            ]
        ) ||
        CATEGORY_IDS[
            categoryKey
        ];


    /*
     * META / BOOK NAME
     */

    const meta =
        firstValue(
            hit,
            [
                "meta",
                "book",
                "book_name",
                "bookName",
                "title"
            ]
        );


    /*
     * TEXT
     */

    const text =
        firstValue(
            hit,
            [
                "text",
                "content",
                "body",
                "full_text"
            ]
        );


    /*
     * SNIPPET
     */

    const snippet =
        firstValue(
            hit,
            [
                "snip",
                "snippet",
                "content",
                "text"
            ]
        );


    /*
     * PAGE
     */

    const resultPage =
        firstValue(
            hit,
            [
                "page",
                "page_number",
                "pageNumber",
                "pg"
            ]
        );


    /*
     * URL
     */

    let url =
        firstValue(
            hit,
            [
                "url",
                "link"
            ]
        );


    /*
     * Jika SDK ada book_id tetapi tiada URL,
     * bina URL Turath.
     */

    if (
        !url &&
        bookId
    ) {

        url =
            `https://turath.io/book/${bookId}`;
    }


    /*
     * Pastikan sekurang-kurangnya
     * text atau snippet wujud.
     */

    if (
        !text &&
        !snippet
    ) {

        return null;
    }


    /*
     * RETURN FORMAT YANG SERASI DENGAN app.py
     */

    return {

        /*
         * ID
         */

        book_id:
            safeString(
                bookId
            ),

        author_id:
            safeString(
                authorId
            ),

        category_id:
            safeString(
                categoryId
            ),

        /*
         * Nama kategori
         */

        category:
            CATEGORY_NAMES[
                categoryKey
            ],

        category_name:
            CATEGORY_NAMES[
                categoryKey
            ],

        /*
         * Metadata kitab
         */

        meta:
            safeString(
                meta
            ),

        book:
            safeString(
                meta
            ),

        book_name:
            safeString(
                meta
            ),

        bookName:
            safeString(
                meta
            ),

        /*
         * Petikan
         */

        snippet:
            safeString(
                snippet
            ),

        snip:
            safeString(
                snippet
            ),

        text:
            safeString(
                text ||
                snippet
            ),

        content:
            safeString(
                text ||
                snippet
            ),

        /*
         * Page
         */

        page:
            safeString(
                resultPage
            ),

        page_number:
            safeString(
                resultPage
            ),

        /*
         * URL
         */

        url:
            safeString(
                url
            ),

        link:
            safeString(
                url
            ),

        /*
         * Search info
         */

        search_query:
            query,

        search_page:
            page
    };
}


/*
|--------------------------------------------------------------------------
| UNIQUE KEY
|--------------------------------------------------------------------------
*/

function resultKey(
    item
) {

    return [

        safeString(
            item.book_id
        ),

        safeString(
            item.page
        ),

        safeString(
            item.meta
        ),

        safeString(
            item.text
        )

    ].join("|");
}


/*
|--------------------------------------------------------------------------
| SEARCH ONE PAGE
|--------------------------------------------------------------------------
*/

async function searchOnePage(
    query,
    categoryKey,
    page
) {

    const categoryId =
        validateCategory(
            categoryKey
        );


    try {

        const response =
            await search(
                query,
                {
                    category:
                        categoryId,

                    page,

                    /*
                     * Sesetengah SDK mungkin
                     * mengabaikan limit.
                     * Kita masih hantar.
                     */

                    limit:
                        RESULTS_PER_PAGE
                }
            );


        /*
         * SDK biasanya:
         *
         * {
         *   data: [...]
         * }
         */

        let data = [];


        if (
            Array.isArray(
                response
            )
        ) {

            data =
                response;

        } else if (
            Array.isArray(
                response?.data
            )
        ) {

            data =
                response.data;

        } else if (
            Array.isArray(
                response?.results
            )
        ) {

            data =
                response.results;

        } else if (
            Array.isArray(
                response?.hits
            )
        ) {

            data =
                response.hits;
        }


        console.log(
            `🔎 ${categoryKey} | ` +
            `"${query}" | ` +
            `page=${page} | ` +
            `${data.length} results`
        );


        return data;

    } catch (error) {

        console.error(
            `❌ TURATH SEARCH ERROR | ` +
            `${categoryKey} | ` +
            `page=${page}`
        );

        console.error(
            error?.stack ||
            error?.message ||
            error
        );

        return [];
    }
}


/*
|--------------------------------------------------------------------------
| SEARCH MANY PAGES
|--------------------------------------------------------------------------
*/

async function searchCategoryMany(
    query,
    categoryKey
) {

    validateCategory(
        categoryKey
    );


    const results = [];
    const seen = new Set();


    for (
        let page = 1;
        page <= SEARCH_PAGES;
        page++
    ) {

        const data =
            await searchOnePage(
                query,
                categoryKey,
                page
            );


        /*
         * Tiada data bermaksud
         * kemungkinan sudah sampai page terakhir.
         */

        if (
            data.length === 0
        ) {

            break;
        }


        for (
            const hit
            of data
        ) {

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
                resultKey(
                    item
                );


            if (
                seen.has(key)
            ) {

                continue;
            }


            seen.add(key);

            results.push(
                item
            );
        }
    }


    console.log(
        `📚 ${categoryKey} collected = ${results.length}`
    );


    return results;
}


/*
|--------------------------------------------------------------------------
| COLLECT CATEGORY
|--------------------------------------------------------------------------
*/

async function collectCategory(
    originalQuery,
    categoryKey
) {

    const queries =
        arabicQueries(
            originalQuery
        );


    console.log(
        `🔍 ${categoryKey} queries:`,
        queries
    );


    const all = [];
    const seen = new Set();


    for (
        const query
        of queries
    ) {

        const results =
            await searchCategoryMany(
                query,
                categoryKey
            );


        for (
            const item
            of results
        ) {

            const key =
                resultKey(
                    item
                );


            if (
                seen.has(key)
            ) {

                continue;
            }


            seen.add(key);

            all.push(
                item
            );
        }
    }


    console.log(
        `📦 ${categoryKey} TOTAL UNIQUE = ${all.length}`
    );


    return all;
}


/*
|--------------------------------------------------------------------------
| RESULT SCORE
|--------------------------------------------------------------------------
*/

function scoreResult(
    item,
    originalQuery
) {

    let score = 0;


    const query =
        String(
            originalQuery || ""
        ).toLowerCase();


    const text =
        [
            item.text,
            item.snippet,
            item.meta,
            item.book
        ]
            .map(
                value =>
                    safeString(
                        value
                    ).toLowerCase()
            )
            .join(" ");


    /*
     * Arabic search terms.
     */

    const arabicQs =
        arabicQueries(
            originalQuery
        );


    for (
        const q
        of arabicQs
    ) {

        const words =
            q
                .split(/\s+/)
                .filter(
                    word =>
                        word.length >= 2
                );


        for (
            const word
            of words
        ) {

            if (
                text.includes(
                    word
                )
            ) {

                score += 2;
            }
        }
    }


    /*
     * Keyword Melayu.
     */

    const malayWords =
        query
            .split(/\s+/)
            .filter(
                word =>
                    word.length >= 3
            );


    for (
        const word
        of malayWords
    ) {

        if (
            text.includes(
                word
            )
        ) {

            score += 1;
        }
    }


    /*
     * Text panjang biasanya lebih berguna
     * daripada snippet sahaja.
     */

    if (
        safeString(
            item.text
        ).length > 100
    ) {

        score += 2;
    }


    /*
     * Metadata kitab wujud.
     */

    if (
        item.book
    ) {

        score += 1;
    }


    /*
     * Page awal sedikit keutamaan.
     */

    if (
        Number(
            item.search_page
        ) === 1
    ) {

        score += 1;
    }


    return score;
}


/*
|--------------------------------------------------------------------------
| RANK
|--------------------------------------------------------------------------
*/

function rankAndLimit(
    results,
    originalQuery,
    limit
) {

    const scored =
        results.map(
            item => ({
                ...item,

                relevance:
                    scoreResult(
                        item,
                        originalQuery
                    )
            })
        );


    scored.sort(
        (a, b) => {

            if (
                b.relevance !==
                a.relevance
            ) {

                return (
                    b.relevance -
                    a.relevance
                );
            }


            /*
             * Kalau score sama,
             * text lebih panjang dahulu.
             */

            return (
                safeString(
                    b.text
                ).length -
                safeString(
                    a.text
                ).length
            );
        }
    );


    return scored.slice(
        0,
        limit
    );
}


/*
|--------------------------------------------------------------------------
| NORMAL SEARCH
|--------------------------------------------------------------------------
*/

async function searchNormal(
    originalQuery
) {

    console.log(
        "📘 NORMAL MODE: SYAFII"
    );


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
| COMPARISON SEARCH
|--------------------------------------------------------------------------
*/

async function searchComparison(
    originalQuery
) {

    console.log(
        "⚖️ COMPARISON MODE"
    );


    const categoryResults = {};


    /*
     * Cari semua mazhab.
     */

    const categories = [
        "shafii",
        "hanafi",
        "maliki",
        "hanbali"
    ];


    for (
        const categoryKey
        of categories
    ) {

        try {

            categoryResults[
                categoryKey
            ] =
                await collectCategory(
                    originalQuery,
                    categoryKey
                );

        } catch (error) {

            console.error(
                `❌ CATEGORY ERROR: ${categoryKey}`,
                error
            );

            categoryResults[
                categoryKey
            ] = [];
        }
    }


    /*
     * Pilih jumlah akhir.
     */

    const final = [];


    for (
        const categoryKey
        of categories
    ) {

        const limit =
            COMPARISON_LIMITS[
                categoryKey
            ];


        const selected =
            rankAndLimit(
                categoryResults[
                    categoryKey
                ] || [],
                originalQuery,
                limit
            );


        final.push(
            ...selected
        );


        console.log(
            `✅ ${categoryKey.toUpperCase()} FINAL = ${selected.length}`
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

    const query =
        String(
            originalQuery || ""
        ).trim();


    if (!query) {

        return {

            mode:
                "shafii",

            requested:
                NORMAL_LIMITS,

            count:
                0,

            passages:
                []
        };
    }


    const comparison =
        isMadhhabComparison(
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


    let passages = [];


    if (
        comparison
    ) {

        passages =
            await searchComparison(
                query
            );

    } else {

        passages =
            await searchNormal(
                query
            );
    }


    console.log(
        `🏁 FINAL TURATH RESULTS = ${passages.length}`
    );


    /*
     * Debug ringkas.
     */

    for (
        let i = 0;
        i < Math.min(
            passages.length,
            5
        );
        i++
    ) {

        const item =
            passages[i];

        console.log(
            `📖 RESULT ${i + 1}:`,
            {
                book:
                    item.book,

                page:
                    item.page,

                category:
                    item.category,

                text_length:
                    safeString(
                        item.text
                    ).length
            }
        );
    }


    return {

        success:
            true,

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
| JSON RESPONSE
|--------------------------------------------------------------------------
*/

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

            "Cache-Control":
                "no-store",

            "Content-Length":
                Buffer.byteLength(
                    body
                )
        }
    );


    res.end(
        body
    );
}


/*
|--------------------------------------------------------------------------
| READ REQUEST BODY
|--------------------------------------------------------------------------
*/

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
                chunk => {

                    body +=
                        chunk.toString(
                            "utf8"
                        );


                    /*
                     * Elak body terlalu besar.
                     */

                    if (
                        body.length >
                        1024 * 1024
                    ) {

                        reject(
                            new Error(
                                "Request body terlalu besar"
                            )
                        );

                        req.destroy();
                    }
                }
            );


            req.on(
                "end",
                () => {

                    if (
                        !body.trim()
                    ) {

                        resolve({});
                        return;
                    }


                    try {

                        resolve(
                            JSON.parse(
                                body
                            )
                        );

                    } catch (
                        error
                    ) {

                        reject(
                            new Error(
                                "JSON request tidak sah"
                            )
                        );
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
| URL PATH
|--------------------------------------------------------------------------
*/

function getPath(
    req
) {

    try {

        return new URL(
            req.url,
            `http://${req.headers.host || "localhost"}`
        ).pathname;

    } catch {

        return req.url;
    }
}


/*
|--------------------------------------------------------------------------
| HTTP SERVER
|--------------------------------------------------------------------------
*/

const server =
    http.createServer(
        async (
            req,
            res
        ) => {

            const path =
                getPath(
                    req
                );


            try {

                /*
                 * --------------------------------------------------------
                 * ROOT
                 * --------------------------------------------------------
                 */

                if (
                    req.method === "GET" &&
                    path === "/"
                ) {

                    return sendJson(
                        res,
                        200,
                        {
                            ok: true,

                            service:
                                "turath",

                            message:
                                "Turath search service is running."
                        }
                    );
                }


                /*
                 * --------------------------------------------------------
                 * HEALTH
                 * --------------------------------------------------------
                 */

                if (
                    req.method === "GET" &&
                    path === "/health"
                ) {

                    return sendJson(
                        res,
                        200,
                        {
                            ok: true,

                            service:
                                "turath",

                            host:
                                HOST,

                            port:
                                PORT,

                            search_pages:
                                SEARCH_PAGES,

                            results_per_page:
                                RESULTS_PER_PAGE,

                            categories:
                                CATEGORY_IDS,

                            category_names:
                                CATEGORY_NAMES,

                            normal_limits:
                                NORMAL_LIMITS,

                            comparison_limits:
                                COMPARISON_LIMITS
                        }
                    );
                }


                /*
                 * --------------------------------------------------------
                 * CATEGORIES
                 * --------------------------------------------------------
                 */

                if (
                    req.method === "GET" &&
                    path === "/categories"
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
                 * --------------------------------------------------------
                 * SEARCH
                 * --------------------------------------------------------
                 */

                if (
                    req.method === "POST" &&
                    path === "/search"
                ) {

                    const body =
                        await readBody(
                            req
                        );


                    const query =
                        String(
                            body.query ||
                            body.question ||
                            ""
                        ).trim();


                    if (!query) {

                        return sendJson(
                            res,
                            400,
                            {
                                success:
                                    false,

                                error:
                                    "Query kosong"
                            }
                        );
                    }


                    /*
                     * Python app.py menghantar:
                     *
                     * {
                     *   query: "...",
                     *   comparison: true/false
                     * }
                     *
                     * Kita masih detect sendiri supaya
                     * service tidak bergantung kepada
                     * client.
                     */

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
                 * --------------------------------------------------------
                 * BOOK INFO
                 * --------------------------------------------------------
                 */

                if (
                    req.method === "GET" &&
                    path.startsWith(
                        "/book/"
                    )
                ) {

                    const parts =
                        path.split(
                            "/"
                        );


                    const id =
                        Number(
                            parts[2]
                        );


                    if (
                        !Number.isFinite(
                            id
                        )
                    ) {

                        return sendJson(
                            res,
                            400,
                            {
                                success:
                                    false,

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
                        {
                            success:
                                true,

                            data:
                                result
                        }
                    );
                }


                /*
                 * --------------------------------------------------------
                 * PAGE
                 * --------------------------------------------------------
                 */

                if (
                    req.method === "GET" &&
                    path.startsWith(
                        "/page/"
                    )
                ) {

                    const parts =
                        path.split(
                            "/"
                        );


                    const bookId =
                        Number(
                            parts[2]
                        );


                    const page =
                        Number(
                            parts[3]
                        );


                    if (
                        !Number.isFinite(
                            bookId
                        ) ||
                        !Number.isFinite(
                            page
                        )
                    ) {

                        return sendJson(
                            res,
                            400,
                            {
                                success:
                                    false,

                                error:
                                    "Book ID atau page tidak sah"
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
                        {
                            success:
                                true,

                            data:
                                result
                        }
                    );
                }


                /*
                 * --------------------------------------------------------
                 * 404
                 * --------------------------------------------------------
                 */

                return sendJson(
                    res,
                    404,
                    {
                        success:
                            false,

                        error:
                            "Endpoint tidak ditemui",

                        path
                    }
                );


            } catch (
                error
            ) {

                console.error(
                    "❌ TURATH SERVICE ERROR:"
                );


                console.error(
                    error?.stack ||
                    error?.message ||
                    error
                );


                return sendJson(
                    res,
                    500,
                    {
                        success:
                            false,

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
| SERVER ERROR
|--------------------------------------------------------------------------
*/

server.on(
    "error",
    error => {

        console.error(
            "❌ HTTP SERVER ERROR:"
        );

        console.error(
            error
        );
    }
);


/*
|--------------------------------------------------------------------------
| START SERVER
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
            "📄 RESULTS PER PAGE:",
            RESULTS_PER_PAGE
        );

        console.log(
            "📘 NORMAL LIMITS:",
            NORMAL_LIMITS
        );

        console.log(
            "⚖️ COMPARISON LIMITS:",
            COMPARISON_LIMITS
        );

        console.log(
            "=============================================="
        );
    }
);
