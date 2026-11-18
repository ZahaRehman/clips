export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path: string[] }> };

const HOP = new Set(["host", "connection", "content-length", "transfer-encoding", "keep-alive"]);

function upstream(): string {
  const raw = process.env.CLIPING_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  return raw.replace(/\/$/, "");
}

async function proxy(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const incoming = new URL(req.url);
  const target = `${upstream()}/${path.join("/")}${incoming.search}`;
  const headers = new Headers(req.headers);
  for (const name of HOP) headers.delete(name);
  const token = (process.env.CLIPING_API_TOKEN || "").trim();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
    headers.set("X-API-Token", token);
  }
  const init: RequestInit & { duplex?: "half" } = { method: req.method, headers, redirect: "manual" };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = req.body;
    init.duplex = "half";
  }
  let res: Response;
  try {
    res = await fetch(target, init);
  } catch {
    return Response.json(
      { error: { code: "API_UNREACHABLE", message: "Cliping API is not reachable" } },
      { status: 502 },
    );
  }
  const out = new Headers(res.headers);
  out.delete("content-encoding");
  out.delete("content-length");
  out.delete("transfer-encoding");
  return new Response(res.body, { status: res.status, headers: out });
}

export function GET(req: Request, ctx: Ctx) {
  return proxy(req, ctx);
}

export function POST(req: Request, ctx: Ctx) {
  return proxy(req, ctx);
}

export function PATCH(req: Request, ctx: Ctx) {
  return proxy(req, ctx);
}

export function PUT(req: Request, ctx: Ctx) {
  return proxy(req, ctx);
}

export function DELETE(req: Request, ctx: Ctx) {
  return proxy(req, ctx);
}

export function HEAD(req: Request, ctx: Ctx) {
  return proxy(req, ctx);
}
