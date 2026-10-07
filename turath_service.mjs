import http from "node:http";
import { search, getBookInfo, getPage } from "turath-sdk";

const PORT = 8765;

function sendJson(res, data, status = 200) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*"
  });

  res.end(JSON.stringify(data));
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://127.0.0.1:${PORT}`);

    // Health check
    if (url.pathname === "/health") {
      return sendJson(res, {
        ok: true,
        service: "turath"
      });
    }

    // Search Turath
    if (url.pathname === "/search") {
      const q = url.searchParams.get("q");

      if (!q) {
        return sendJson(res, {
          error: "Parameter q diperlukan"
        }, 400);
      }

      console.log("🔎 TURATH SEARCH:", q);

      const result = await search(q);

      return sendJson(res, {
        ok: true,
        query: q,
        result
      });
    }

    // Book information
    if (url.pathname.startsWith("/book/")) {
      const id = url.pathname.split("/")[2];

      if (!id) {
        return sendJson(res, {
          error: "Book ID diperlukan"
        }, 400);
      }

      console.log("📚 TURATH BOOK:", id);

      const result = await getBookInfo(id);

      return sendJson(res, {
        ok: true,
        book_id: id,
        result
      });
    }

    // Page
    if (url.pathname.startsWith("/page/")) {
      const parts = url.pathname.split("/");

      const bookId = parts[2];
      const pageNumber = Number(parts[3]);

      if (!bookId || !pageNumber) {
        return sendJson(res, {
          error: "Book ID dan page diperlukan"
        }, 400);
      }

      console.log(
        `📖 TURATH PAGE: ${bookId} / ${pageNumber}`
      );

      const result = await getPage(
        bookId,
        pageNumber
      );

      return sendJson(res, {
        ok: true,
        book_id: bookId,
        page: pageNumber,
        result
      });
    }

    return sendJson(res, {
      error: "Endpoint tidak dijumpai"
    }, 404);

  } catch (error) {

    console.error("❌ TURATH ERROR:", error);

    return sendJson(res, {
      ok: false,
      error: error.message || String(error)
    }, 500);
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(
    `🚀 Turath service running on http://127.0.0.1:${PORT}`
  );
});
