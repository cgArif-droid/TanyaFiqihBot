import http from "node:http";
import {
  search,
  getBookInfo,
  getPage
} from "turath-sdk";

const PORT = 8765;

function sendJson(res, data, status = 200) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*"
  });

  res.end(JSON.stringify(data));
}

function normalizeSearchResult(item) {
  if (!item || typeof item !== "object") {
    return null;
  }

  const bookId =
    item.bookId ??
    item.book_id ??
    item.book?.id ??
    item.book?.bookId ??
    item.metadata?.bookId ??
    null;

  const pageId =
    item.pageId ??
    item.page_id ??
    item.page?.id ??
    item.page?.pageId ??
    item.location?.pageId ??
    item.location?.internalPage ??
    item.metadata?.pageId ??
    null;

  const title =
    item.bookTitle ??
    item.book_title ??
    item.book?.title ??
    item.title ??
    item.metadata?.bookTitle ??
    "";

  const author =
    item.authorName ??
    item.author_name ??
    item.author?.name ??
    item.metadata?.authorName ??
    "";

  const text =
    item.text ??
    item.content ??
    item.snippet ??
    item.highlight ??
    "";

  return {
    book_id: bookId,
    page_id: pageId,
    title,
    author,
    text,
    raw: item
  };
}

async function searchTurath(query) {
  console.log("🔎 TURATH SEARCH:", query);

  const result = await search(query);

  console.log(
    "📊 TURATH SEARCH RESULT:",
    JSON.stringify(result).slice(0, 3000)
  );

  const data = Array.isArray(result)
    ? result
    : result?.data ??
      result?.results ??
      result?.items ??
      [];

  const hits = data
    .map(normalizeSearchResult)
    .filter(Boolean);

  const passages = [];

  for (const hit of hits.slice(0, 10)) {
    let page = null;

    if (
      hit.book_id !== null &&
      hit.page_id !== null
    ) {
      try {
        page = await getPage(
          Number(hit.book_id),
          Number(hit.page_id)
        );
      } catch (error) {
        console.error(
          "❌ TURATH getPage error:",
          error.message
        );
      }
    }

    const pageText =
      page?.text ??
      page?.content ??
      page?.data?.text ??
      page?.result?.text ??
      "";

    const pageMetadata =
      page?.metadata ??
      page?.data?.metadata ??
      {};

    const finalText =
      pageText ||
      hit.text ||
      "";

    if (!finalText.trim()) {
      continue;
    }

    passages.push({
      source: "turath",
      book_id: hit.book_id,
      page_id: hit.page_id,
      title:
        pageMetadata?.bookTitle ??
        pageMetadata?.book_title ??
        hit.title ??
        "Kitab Turath",
      author:
        pageMetadata?.authorName ??
        pageMetadata?.author_name ??
        hit.author ??
        "",
      text: finalText
    });
  }

  return {
    ok: true,
    query,
    count: passages.length,
    passages
  };
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(
      req.url,
      `http://127.0.0.1:${PORT}`
    );

    // =========================
    // HEALTH
    // =========================

    if (url.pathname === "/health") {
      return sendJson(res, {
        ok: true,
        service: "turath",
        sdk: "turath-sdk"
      });
    }

    // =========================
    // SEARCH
    // =========================

    if (url.pathname === "/search") {
      const q = url.searchParams.get("q");

      if (!q) {
        return sendJson(
          res,
          {
            ok: false,
            error: "Parameter q diperlukan"
          },
          400
        );
      }

      const result = await searchTurath(q);

      return sendJson(res, result);
    }

    // =========================
    // BOOK INFO
    // =========================

    if (url.pathname.startsWith("/book/")) {
      const id = url.pathname.split("/")[2];

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

      console.log("📚 TURATH BOOK:", id);

      const result = await getBookInfo(
        Number(id)
      );

      return sendJson(res, {
        ok: true,
        book_id: Number(id),
        result
      });
    }

    // =========================
    // PAGE
    // =========================

    if (url.pathname.startsWith("/page/")) {
      const parts = url.pathname.split("/");

      const bookId = parts[2];
      const pageNumber = parts[3];

      if (!bookId || !pageNumber) {
        return sendJson(
          res,
          {
            ok: false,
            error: "Book ID dan page diperlukan"
          },
          400
        );
      }

      console.log(
        `📖 TURATH PAGE: ${bookId} / ${pageNumber}`
      );

      const result = await getPage(
        Number(bookId),
        Number(pageNumber)
      );

      return sendJson(res, {
        ok: true,
        book_id: Number(bookId),
        page: Number(pageNumber),
        result
      });
    }

    // =========================
    // 404
    // =========================

    return sendJson(
      res,
      {
        ok: false,
        error: "Endpoint tidak dijumpai"
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
