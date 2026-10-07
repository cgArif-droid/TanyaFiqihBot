import http from "node:http";
import {
  search,
  getBookInfo,
  getBookFile,
  getAuthor,
  getPage
} from "turath-sdk";

const PORT = 8765;

function sendJson(res, data, status = 200) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*"
  });

  res.end(
    JSON.stringify(
      data,
      null,
      2
    )
  );
}


// =========================================================
// HEALTH
// =========================================================

function health(res) {
  return sendJson(res, {
    ok: true,
    service: "turath",
    sdk: "turath-sdk",
    status: "running"
  });
}


// =========================================================
// SEARCH
// =========================================================

async function searchTurath(q) {

  console.log(
    "🔎 TURATH SEARCH:",
    q
  );

  const result = await search(
    q
  );

  console.log(
    `📊 TURATH COUNT: ${result.count}`
  );

  console.log(
    `📊 TURATH DATA: ${result.data.length}`
  );

  const passages = result.data.map(
    (item) => {

      const meta =
        item.meta || {};

      return {
        source_type: "turath",

        book_id:
          item.book_id ?? null,

        author_id:
          item.author_id ?? null,

        category_id:
          item.cat_id ?? null,

        page:
          meta.page ?? null,

        page_id:
          meta.page_id ?? null,

        volume:
          meta.vol ?? null,

        kitab_name:
          meta.book_name ??
          "Kitab Turath",

        author:
          meta.author_name ??
          "",

        headings:
          meta.headings ??
          [],

        text:
          item.text ??
          "",

        snippet:
          item.snip ??
          "",

        url:
          item.book_id
            ? `https://app.turath.io/book/${item.book_id}`
            : null
      };
    }
  );

  return {
    ok: true,
    query: q,
    count: result.count,
    passages
  };
}


// =========================================================
// BOOK INFO
// =========================================================

async function bookInfo(id) {

  console.log(
    "📚 TURATH BOOK:",
    id
  );

  const result =
    await getBookInfo(
      Number(id)
    );

  return {
    ok: true,
    book_id: Number(id),
    result
  };
}


// =========================================================
// BOOK FILE
// =========================================================

async function bookFile(id) {

  console.log(
    "📦 TURATH BOOK FILE:",
    id
  );

  const result =
    await getBookFile(
      Number(id)
    );

  return {
    ok: true,
    book_id: Number(id),
    result
  };
}


// =========================================================
// AUTHOR
// =========================================================

async function authorInfo(id) {

  console.log(
    "👤 TURATH AUTHOR:",
    id
  );

  const result =
    await getAuthor(
      Number(id)
    );

  return {
    ok: true,
    author_id: Number(id),
    result
  };
}


// =========================================================
// PAGE
// =========================================================

async function pageInfo(
  bookId,
  pageNumber
) {

  console.log(
    `📖 TURATH PAGE: ${bookId} / ${pageNumber}`
  );

  const result =
    await getPage(
      Number(bookId),
      Number(pageNumber)
    );

  return {
    ok: true,

    book_id:
      Number(bookId),

    page:
      Number(pageNumber),

    text:
      result.text ?? "",

    metadata:
      result.meta ?? {},

    result
  };
}


// =========================================================
// HTTP SERVER
// =========================================================

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


        // -------------------------------------------------
        // HEALTH
        // -------------------------------------------------

        if (
          url.pathname ===
          "/health"
        ) {

          return health(
            res
          );
        }


        // -------------------------------------------------
        // SEARCH
        // -------------------------------------------------

        if (
          url.pathname ===
          "/search"
        ) {

          const q =
            url.searchParams.get(
              "q"
            );

          if (!q) {

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
            await searchTurath(
              q
            );

          return sendJson(
            res,
            result
          );
        }


        // -------------------------------------------------
        // BOOK INFO
        // -------------------------------------------------

        if (
          url.pathname.startsWith(
            "/book/"
          )
        ) {

          const id =
            url.pathname
              .split("/")
              [2];

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
            await bookInfo(
              id
            );

          return sendJson(
            res,
            result
          );
        }


        // -------------------------------------------------
        // BOOK FILE
        // -------------------------------------------------

        if (
          url.pathname.startsWith(
            "/book-file/"
          )
        ) {

          const id =
            url.pathname
              .split("/")
              [2];

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
            await bookFile(
              id
            );

          return sendJson(
            res,
            result
          );
        }


        // -------------------------------------------------
        // AUTHOR
        // -------------------------------------------------

        if (
          url.pathname.startsWith(
            "/author/"
          )
        ) {

          const id =
            url.pathname
              .split("/")
              [2];

          if (!id) {

            return sendJson(
              res,
              {
                ok: false,
                error:
                  "Author ID diperlukan"
              },
              400
            );
          }

          const result =
            await authorInfo(
              id
            );

          return sendJson(
            res,
            result
          );
        }


        // -------------------------------------------------
        // PAGE
        // -------------------------------------------------

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
            parts[2];

          const pageNumber =
            parts[3];

          if (
            !bookId ||
            pageNumber ===
              undefined
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
            await pageInfo(
              bookId,
              pageNumber
            );

          return sendJson(
            res,
            result
          );
        }


        // -------------------------------------------------
        // 404
        // -------------------------------------------------

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
          error
        );

        return sendJson(
          res,
          {
            ok: false,
            error:
              error?.message ??
              String(error)
          },
          500
        );
      }
    }
  );


// =========================================================
// START
// =========================================================

server.listen(
  PORT,
  "127.0.0.1",
  () => {

    console.log(
      "=========================================="
    );

    console.log(
      "🚀 TURATH SERVICE STARTED"
    );

    console.log(
      `📡 http://127.0.0.1:${PORT}`
    );

    console.log(
      "📚 SDK: turath-sdk"
    );

    console.log(
      "=========================================="
    );
  }
);
