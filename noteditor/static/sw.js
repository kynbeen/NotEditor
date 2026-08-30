"use strict";

const CACHE_NAME = "noteditor-shell-v2";
const APP_SHELL = [
  "/index.html",
  "/app.css",
  "/app.js",
  "/manifest.webmanifest",
  "/icons/icon-180.png",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)),
    )),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  event.respondWith((async () => {
    try {
      const response = await fetch(event.request);
      // 서버가 사용자별 응답이라고 표시한 것은 절대 저장하지 않는다. 첫 화면은 세션 쿠키를
      // 발급하는 응답이라 여기 걸린다.
      const noStore = (response.headers.get("Cache-Control") || "").includes("no-store");
      if (response.ok && !noStore) {
        const cache = await caches.open(CACHE_NAME);
        await cache.put(event.request, response.clone());
      }
      return response;
    } catch (error) {
      const cached = await caches.match(event.request, { ignoreSearch: true });
      if (cached) return cached;
      if (event.request.mode === "navigate") return caches.match("/index.html");
      throw error;
    }
  })());
});
