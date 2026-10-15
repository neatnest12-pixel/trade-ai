import { NextRequest, NextResponse } from "next/server";

const baseUrl = process.env.NAVORA_BACKEND_URL;
const apiKey = process.env.NAVORA_API_SECRET;

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (!baseUrl || !apiKey) return NextResponse.json({ error: "Dashboard backend is not configured" }, { status: 503 });
  const { path } = await context.params;
  const target = new URL(path.join("/"), baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  target.search = request.nextUrl.search;
  const response = await fetch(target, { headers: { "X-Navora-API-Key": apiKey }, cache: "no-store" });
  return new NextResponse(response.body, { status: response.status, headers: { "content-type": response.headers.get("content-type") ?? "application/json" } });
}