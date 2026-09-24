# Redesign Halaman Agent — Panduan Implementasi

Dokumen ini ditujukan untuk developer yang baru masuk ke proyek ini. Asumsinya kamu
bisa React dan TypeScript, tapi belum tahu apa-apa soal kode di repo ini. Semua
konteks yang kamu butuhkan ada di dokumen ini.

Baca **seluruh** bagian "Latar Belakang" dulu sebelum menyentuh kode. Banyak keputusan
di sini sudah melewati diskusi panjang dan punya alasan yang tidak kelihatan dari
kodenya. Kalau kamu melanggarnya tanpa tahu alasannya, kamu akan merusak hal lain.

---

## Daftar Isi

1. [Latar Belakang](#1-latar-belakang)
2. [Keputusan Yang Sudah Final](#2-keputusan-yang-sudah-final)
3. [Hal Yang Tidak Boleh Kamu Lakukan](#3-hal-yang-tidak-boleh-kamu-lakukan)
4. [Struktur File Target](#4-struktur-file-target)
5. [Fase 0 — Persiapan](#fase-0--persiapan)
6. [Fase 1 — Fungsi Murni + Test](#fase-1--fungsi-murni--test)
7. [Fase 2 — Shell & Layout Empat Kolom](#fase-2--shell--layout-empat-kolom)
8. [Fase 3 — Kanvas & Kartu Node](#fase-3--kanvas--kartu-node)
9. [Fase 4 — Topbar, Save, Dirty Tracking, Konflik](#fase-4--topbar-save-dirty-tracking-konflik)
10. [Fase 5 — Panel Kanan Bertab](#fase-5--panel-kanan-bertab)
11. [Fase 6 — Palette](#fase-6--palette)
12. [Fase 7 — Diagnostics](#fase-7--diagnostics)
13. [Fase 8 — Pembersihan](#fase-8--pembersihan)
14. [Lampiran A — Bentuk Dokumen Agent](#lampiran-a--bentuk-dokumen-agent)
15. [Lampiran B — Endpoint Backend](#lampiran-b--endpoint-backend)

---

## 1. Latar Belakang

### Apa yang sedang kita bangun

Aplikasi ini adalah **agent orchestration studio**. User membuat "agent" yang
sebenarnya adalah sebuah graf: kumpulan node (agent kecil atau tool) yang terhubung
oleh edge (aturan alur eksekusi). Backend menjalankan graf itu pakai LangGraph.

Halaman Agent adalah tempat user menyusun graf itu. Saat ini halaman tersebut
berfungsi tapi tampilannya tumpukan form vertikal. Kita akan mengubahnya jadi
**editor kanvas**: kanvas memenuhi layar, semua kontrol mengambang di atasnya.

### Referensi desain

Ada aplikasi bernama PINTER yang jadi acuan **anatomi** (tata letaknya), bukan
warnanya. Yang kita ambil:

- Kanvas full-bleed — kanvas adalah halamannya, bukan kotak di dalam halaman
- Empat kolom: rail ikon → palette → kanvas → panel inspector
- Topbar kontekstual mengambang di atas kanvas
- Node digambar sebagai kartu, bukan kotak polos
- Panel kanan punya tab Config / Test / History
- Container berlabel membungkus node-node yang berjalan paralel
- Legend dan kontrol zoom mengambang di pojok kanvas

Yang **tidak** kita ambil: palet warna PINTER (dark navy + neon). Warna kita tetap
mengikuti `design.md` di root repo (ivory/terracotta, light + dark mode).

### Kondisi kode sekarang

Frontend ada di `frontend/`. Stack-nya:

| Hal | Yang dipakai |
|---|---|
| Framework | React 19 |
| Build | Vite 8 |
| Styling | Tailwind v4 (CSS-first, token di `src/index.css` pakai `@theme`) + `App.css` |
| Kanvas | `@xyflow/react` v12 (React Flow) |
| Ikon | `lucide-react` |
| Package manager | **bun** (bukan npm, bukan yarn) |
| Linter | **oxlint** (`bun run lint`) |
| Test | **belum ada** — kamu yang akan menambahkannya |

Catatan: `@tanstack/react-query` terpasang di `package.json` tapi **belum dipakai
sama sekali**. `api.ts` memakai `fetch` telanjang. Jangan tergoda memperkenalkan
react-query dalam pekerjaan ini — itu refactor terpisah.

File yang relevan:

```
frontend/src/
  App.tsx                      326 baris  shell: sidebar + topbar + switch tab
  api.ts                       643 baris  semua tipe TypeScript + client fetch
  index.css                               token @theme Tailwind v4
  App.css                                 kelas CSS lama (.card, .btn, .sidebar, dst)
  components/
    AgentsView.tsx             587 baris  DUA mode dalam satu file: list + editor
    GraphCanvas.tsx           1054 baris  4 tab + ReactFlow + node inline-style
    AgentInspector.tsx         658 baris  accordion config agent
    ConversationsView.tsx      533 baris
    ToolsView.tsx              577 baris
    ModelsView.tsx             388 baris
    KnowledgeView.tsx          251 baris
    CredentialsView.tsx        186 baris
```

**Penting:** `read_file` pada `GraphCanvas.tsx`, `AgentsView.tsx`, dan
`AgentInspector.tsx` akan mengembalikan *outline* (daftar nama fungsi + nomor baris),
bukan isi file, karena file-nya terlalu besar. Gunakan nomor baris dari outline itu
untuk membaca bagian tertentu dengan parameter `start_line` / `end_line`.

### Cara kerja halaman Agent sekarang

`AgentsView.tsx` punya dua mode dalam satu komponen:

1. **Mode list** — grid kartu agent. Klik kartu memanggil `selectAgent(id)`.
2. **Mode editor** — aktif kalau `activeAgent && draftDoc` terisi. Merender di dalam
   `<div className="view-container"><div className="card editor-page">`, isinya
   ditumpuk vertikal: topbar → banner status → panel diagnostics → `<GraphCanvas>`
   → run panel.

`GraphCanvas.tsx` punya empat tab sendiri: Visual, Nodes, Edges, Topology Settings.
Di tab Visual, `AgentInspector` di-embed sebagai pane kanan.

Node dirender sebagai JSX di dalam `data.label` dengan **inline style** dan warna hex
hardcoded (`#141311`, `#d97757`, `#8a867c`). Artinya kanvas tetap gelap walaupun
aplikasi sedang di light mode. Itu bug yang sudah ada, dan pekerjaan ini akan
memperbaikinya sebagai efek samping.

Posisi node dihitung dengan `idx % 3` — grid kaku tiga kolom. Tidak ada persistensi
dan tidak ada hubungannya dengan struktur graf.

---

## 2. Keputusan Yang Sudah Final

Sebelas keputusan ini sudah disepakati. Jangan ubah tanpa bertanya.

| # | Keputusan | Alasan singkat |
|---|---|---|
| 1 | Ambil anatomi PINTER, pertahankan token warna `design.md` | Palet mengikat 6 view lain; mengubahnya = proyek terpisah |
| 2 | Posisi node disimpan di `localStorage` per agent, dagre sebagai dasar | Nol perubahan backend; `content_hash` tidak tercemar koordinat |
| 3 | Mesin layout: `@dagrejs/dagre` | Matang, kecil, TypeScript-native |
| 4 | Empat tab `GraphCanvas` dihapus semua | Diganti kanvas + panel kanan kontekstual |
| 5 | Himpunan node berubah → buang seluruh cache posisi, layout ulang penuh | Prediktabel, satu fungsi murni |
| 6 | Palette permanen kolom kedua, search + drag-to-create | Mengikuti anatomi referensi |
| 7 | Group `PARALLEL FLOW` diturunkan dari edge `join` | Informasinya sudah ada di dokumen; nol perubahan skema |
| 8 | Save manual + dirty tracking + dialog konflik 409 | Autosave + optimistic locking = banjir 409 |
| 9 | Diagnostics: badge di kartu node + daftar mengambang | Badge saja menyembunyikan daftar; daftar saja tidak menunjuk lokasi |
| 10 | Editor lolos dari shell; sidebar menciut jadi rail ikon | Kanvas adalah inti halaman |
| 11 | Test runner: Vitest | Logika graf punya banyak cabang, wajib punya tabel kasus |

Arsitektur komponen: **props + komposisi**. Satu `useReducer` di `EditorShell`,
diturunkan lewat props. Tidak ada zustand, tidak ada context, tidak ada state
management library.

> Catatan soal performa: `zustand@4.5.7` sebenarnya sudah ada di bundle karena React
> Flow memakainya secara internal. Kita tetap **tidak** memakainya. Untuk graf
> orkestrasi ukuran realistis (belasan sampai dua puluhan node), props sudah cukup.
> Peredamnya ada di Fase 3: `React.memo` + `data` yang dipersempit.

---

## 3. Hal Yang Tidak Boleh Kamu Lakukan

Baca ini dua kali.

**Jangan simpan posisi node ke dalam dokumen agent.**
`content_hash` adalah SHA-256 dari canonical JSON **seluruh dokumen** (lihat
`backend/src/orchestrator/domain/canonical.py`). Kalau koordinat piksel masuk ke
dokumen, menggeser node sedikit saja akan menghasilkan revisi baru dengan perilaku
runtime yang identik. Riwayat revisi jadi sampah. Posisi hidup di `localStorage`,
titik.

**Jangan ubah palet warna.**
`design.md` melarang: gradient, warna neon, biru default Tailwind, animasi lebih dari
300ms, dan kontras di bawah 4.5:1. Pakai token yang sudah ada
(`var(--color-fg)`, `var(--color-accent)`, `var(--color-border)`, dst). Kalau kamu
butuh warna yang belum ada tokennya, tambahkan token di `index.css`, jangan tulis hex
di komponen.

**Jangan tambah dependency selain dua yang disebut.**
Hanya `@dagrejs/dagre` dan `vitest` yang disetujui. Kalau kamu merasa butuh yang lain,
berhenti dan tanya.

**Jangan pakai `dagre` (tanpa scope).**
Paket lama itu sudah tidak dipelihara. Yang benar `@dagrejs/dagre`. Paket ini sudah
membawa tipe TypeScript sendiri — **jangan** install `@types/dagre`, akan bentrok.

**Jangan sentuh backend.**
Seluruh pekerjaan ini frontend saja. Semua endpoint yang dibutuhkan sudah ada.

**Jangan refactor 6 view lain.**
`ConversationsView`, `ToolsView`, `ModelsView`, `KnowledgeView`, `CredentialsView`
tidak disentuh. Mode list di `AgentsView` juga tidak di-redesign — hanya mode editor.

---

## 4. Struktur File Target

```
frontend/src/components/agent-editor/
  EditorShell.tsx          layout 4 kolom, useReducer, orkestrasi
  useAgentEditor.ts        reducer + tipe aksi + tipe state
  Topbar.tsx               nama, badge, aksi, dialog konflik
  Palette.tsx              search + daftar agent + drag-to-create
  canvas/
    Canvas.tsx             ReactFlow, onConnect, legend, zoom
    AgentNodeCard.tsx      custom node type, kelas Tailwind
    ExitNode.tsx           custom node type untuk named exit
    ParallelGroup.tsx      overlay bounding box
  panel/
    InspectorPanel.tsx     switch tiga konteks + tab
    ConfigTab.tsx          dipindah dari AgentInspector.tsx
    EdgeEditor.tsx         form edge (5 kind)
    TopologyForm.tsx       entry_node_id, named_exits, recursion_limit
    TestTab.tsx            dipindah dari run panel AgentsView
    HistoryTab.tsx         GET /agents/{id}/revisions
  lib/
    edges.ts               extractEdgePairs
    layout.ts              runLayout, deriveGroups
    positions.ts           cache localStorage + signature
    graph.test.ts          Vitest untuk keempat fungsi di atas
```

Di akhir pekerjaan, `GraphCanvas.tsx` **dihapus sepenuhnya** dan `AgentsView.tsx`
menyusut jadi mode list saja.

---

## Fase 0 — Persiapan

### 0.1 Pasang dependency

```sh
cd frontend
bun add @dagrejs/dagre
bun add -d vitest
```

### 0.2 Tambah skrip test

Di `frontend/package.json`, bagian `scripts`, tambahkan:

```json
"test": "vitest run",
"test:watch": "vitest"
```

Vitest memakai ulang `vite.config.ts` yang sudah ada. Tidak perlu file konfigurasi
terpisah.

### 0.3 Verifikasi

```sh
bun run test
```

Harus jalan dan bilang tidak ada test (belum ada file test). Kalau error selain itu,
selesaikan dulu sebelum lanjut.

### 0.4 Buat direktori

Buat seluruh struktur direktori di [Bagian 4](#4-struktur-file-target). Biarkan
kosong dulu.

---

## Fase 1 — Fungsi Murni + Test

Ini fase paling penting dan harus dikerjakan **pertama**. Empat fungsi di sini adalah
otak dari seluruh kanvas. Kalau salah, mereka tidak crash — mereka diam-diam
menggambar graf yang keliru. Itu jauh lebih sulit di-debug daripada error.

Semua fungsi di fase ini **murni**: tidak menyentuh React, tidak menyentuh DOM,
tidak memanggil `fetch`. Input masuk, output keluar. Itulah kenapa mereka bisa diuji
dengan mudah.

### 1.1 `lib/edges.ts` — `extractEdgePairs`

**Masalah yang dipecahkan:** dokumen punya lima jenis edge dengan bentuk data yang
sangat berbeda. Dagre butuh daftar pasangan `(source, target)` yang seragam. Fungsi
ini yang meratakannya.

Ini bentuk kelima jenis edge (baca [Lampiran A](#lampiran-a--bentuk-dokumen-agent)
untuk detail lengkap):

| kind | field | di mana target-nya |
|---|---|---|
| `direct` | `source`, `target` | `target` |
| `exit` | `source`, `result_name` | named exit, bukan node |
| `join` | `sources[]`, `target`, `join` | `target`, sumbernya banyak |
| `semantic` | `source`, `field`, `routes`, `default` | **di dalam `routes`** + `default` |
| `mechanical` | `source`, `field`, `operator`, `value`, `then`, `else` | **`then` dan `else`** |

**Jebakan besar yang harus kamu tahu:** untuk `semantic` dan `mechanical`, field
`source` **bukan string**. Dia array dua elemen: `[nodeId, fieldName]`. Kode lama
membacanya sebagai `e.source?.[0]` dan `e.source?.[1]`. Kalau kamu memperlakukannya
sebagai string, `source[0]` akan mengembalikan huruf pertama dari nama node dan
semuanya akan diam-diam salah.

Logika ini sudah ada di repo, tapi ter-inline di dalam `rfEdges` di
`GraphCanvas.tsx` sekitar baris 237–332. **Baca bagian itu dulu** sebelum menulis.
Tugasmu memindahkannya jadi fungsi murni, bukan menemukannya ulang.

Tanda tangan yang diharapkan:

```ts
export interface EdgePair {
  /** index edge di array document.edges — untuk memetakan diagnostic */
  edgeIndex: number
  /** node ID sumber */
  source: string
  /** node ID target, atau `__exit_<name>` untuk named exit */
  target: string
  /** label yang ditampilkan di edge */
  label?: string
  /** kind asal, untuk menentukan warna/gaya garis */
  kind: 'direct' | 'exit' | 'join' | 'semantic' | 'mechanical'
  /** true untuk cabang default/else — digambar lebih tipis */
  isFallback?: boolean
}

export function extractEdgePairs(
  edges: any[],
  namedExits: string[]
): EdgePair[]
```

Aturan penamaan target exit: kalau target sebuah route/then/else namanya ada di
`namedExits`, ubah jadi `__exit_<nama>`. Kalau tidak, dia node biasa. Kode lama
melakukan ini dengan `namedExits.includes(target) ? `__exit_${target}` : target`.

**Kasus test yang wajib ada:**

1. `direct` menghasilkan satu pasang
2. `exit` menghasilkan satu pasang dengan target `__exit_success`
3. `join` dengan tiga `sources` menghasilkan tiga pasang ke target yang sama
4. `semantic` dengan dua route menghasilkan dua pasang, label berisi nama field dan
   nilainya
5. `semantic` dengan `default` menghasilkan pasang tambahan bertanda `isFallback`
6. `semantic` yang me-route ke named exit menghasilkan target `__exit_<nama>`
7. `mechanical` dengan `then` dan `else` menghasilkan dua pasang
8. `mechanical` tanpa `else` menghasilkan satu pasang
9. Array `edges` kosong menghasilkan array kosong
10. `join` dengan `sources` kosong tidak crash
11. `semantic` dengan `source` array `[nodeId, fieldName]` mengambil `nodeId` yang
    benar sebagai sumber — **ini test paling penting di seluruh file**

### 1.2 `lib/layout.ts` — `runLayout`

Pembungkus tipis di atas dagre.

**Apa itu dagre.** Dagre adalah pustaka JavaScript yang menghitung koordinat node
untuk graf berarah. Kamu beri dia daftar node (dengan ukuran) dan daftar edge; dia
kembalikan `x`/`y` tiap node. Algoritmanya gaya Sugiyama, empat tahap:

1. **Pemeringkatan** — tiap node ditaruh di lapisan. Node tanpa edge masuk jadi
   lapisan 0, sisanya didorong ke bawah agar edge selalu mengarah maju. Siklus
   dipatahkan sementara dengan membalik sebagian edge.
2. **Pengurutan** — urutan node dalam tiap lapisan diatur untuk meminimalkan
   persilangan edge.
3. **Penempatan** — koordinat diberikan, menghormati `nodesep` (jarak antar node satu
   lapisan) dan `ranksep` (jarak antar lapisan).
4. **Perutean edge** — titik belok dikembalikan; kita abaikan, React Flow menggambar
   kurvanya sendiri.

Efeknya untuk kita: node hulu selalu di atas node hilir, dan cabang paralel otomatis
mendarat di lapisan yang sama. Itulah yang membuat `deriveGroups` di bawah bisa
bekerja tanpa usaha tambahan.

```ts
export const NODE_WIDTH = 240
export const NODE_HEIGHT = 96

export interface LayoutResult {
  positions: Record<string, { x: number; y: number }>
}

export function runLayout(
  nodeIds: string[],
  pairs: EdgePair[]
): LayoutResult
```

Konfigurasi dagre:

```ts
g.setGraph({ rankdir: 'TB', nodesep: 48, ranksep: 72 })
g.setDefaultEdgeLabel(() => ({}))
```

Dagre mengembalikan koordinat **pusat** node. React Flow memakai **pojok kiri atas**.
Jadi kamu harus mengurangi setengah lebar dan setengah tinggi:

```ts
positions[id] = {
  x: node.x - NODE_WIDTH / 2,
  y: node.y - NODE_HEIGHT / 2,
}
```

Kalau kamu lupa konversi ini, semua node akan bergeser dan edge akan terlihat
menempel di tempat yang salah.

Edge yang menunjuk ke node yang tidak ada di `nodeIds` harus **dilewati**, bukan
bikin crash. Dokumen bisa dalam keadaan invalid saat user sedang mengedit — itu
normal, validator backend yang akan mengeluh, bukan kanvas.

**Kasus test:**

1. Rantai linear tiga node → `y` menaik, `x` sama
2. Fan-out (satu node ke dua node) → dua anak punya `y` sama, `x` berbeda
3. Node terisolasi (tanpa edge) tetap dapat posisi
4. Edge menunjuk node tidak dikenal → dilewati, tidak crash
5. Input kosong → objek kosong
6. Posisi yang dikembalikan adalah pojok kiri atas, bukan pusat (uji dengan satu node
   tunggal dan hitung manual)

### 1.3 `lib/layout.ts` — `deriveGroups`

Menghasilkan container `PARALLEL FLOW`. Dokumen kita **tidak punya** konsep group —
tidak ada field untuk itu. Group diturunkan dari topologi: edge `join` dengan
`sources[]` secara struktural berarti "beberapa cabang bergabung kembali di sini".

Algoritma:

1. Untuk tiap edge `join`, ambil `sources[]`
2. Telusuri mundur dari tiap source sampai ketemu node leluhur bersama (titik
   percabangan)
3. Node di antara titik percabangan dan `join` adalah anggota group
4. Hitung bounding box dari posisi hasil `runLayout`, tambah padding

```ts
export interface ParallelGroup {
  id: string
  label: string
  nodeIds: string[]
  x: number
  y: number
  width: number
  height: number
}

export function deriveGroups(
  pairs: EdgePair[],
  positions: Record<string, { x: number; y: number }>,
  edges: any[]
): ParallelGroup[]
```

**Batasan yang disengaja:** kalau terdeteksi group bersarang (satu group berisi group
lain) atau dua group yang anggotanya tumpang tindih, **kembalikan array kosong**.
Bounding box yang saling potong lebih buruk daripada tidak ada group sama sekali.
Tulis komentar di kode yang menjelaskan ini, supaya orang berikutnya tidak mengira
itu bug.

**Kasus test:**

1. Fan-out lalu `join` → satu group berisi node cabang
2. Graf linear tanpa `join` → array kosong
3. Dua `join` terpisah tidak tumpang tindih → dua group
4. Group bersarang → array kosong (jalur bail-out)
5. Bounding box benar-benar melingkupi semua anggota + padding

### 1.4 `lib/positions.ts` — cache localStorage

Menerapkan keputusan 5: **begitu himpunan node berubah, buang seluruh cache dan
layout ulang penuh.**

Kenapa begitu? Karena alternatifnya lebih buruk. Menyisipkan node baru ke dalam
layout lama butuh mencari ruang kosong, dan hasilnya sering menumpuk. Layout ulang
penuh selalu benar dan selalu bisa diprediksi.

Kunci: `agent-layout:<agentId>`.

Bentuk data yang disimpan:

```ts
interface StoredLayout {
  /** node ID terurut, digabung koma — dipakai deteksi perubahan */
  signature: string
  positions: Record<string, { x: number; y: number }>
}
```

```ts
export function computeSignature(nodeIds: string[]): string

export function loadPositions(
  agentId: string,
  nodeIds: string[]
): Record<string, { x: number; y: number }> | null

export function savePositions(
  agentId: string,
  nodeIds: string[],
  positions: Record<string, { x: number; y: number }>
): void

export function clearPositions(agentId: string): void
```

`loadPositions` mengembalikan `null` kalau:
- tidak ada entri untuk agent itu
- signature tersimpan berbeda dari signature sekarang
- JSON-nya rusak

`null` artinya "jalankan `runLayout`". Pemanggil tidak perlu tahu alasannya.

**Jangan lupa `try/catch` di sekitar `localStorage`.** Di mode private browsing
sebagian browser melempar exception saat menulis. Kegagalan menyimpan posisi node
tidak boleh menjatuhkan editor. Kalau gagal, diam saja — user cuma kehilangan
tata letak manualnya, bukan pekerjaannya.

`computeSignature` harus mengurutkan dulu. `['b','a']` dan `['a','b']` adalah
himpunan yang sama dan harus menghasilkan signature yang sama.

**Kasus test:**

1. `computeSignature` tidak peduli urutan input
2. Simpan lalu muat mengembalikan posisi yang sama
3. Muat dengan himpunan node berbeda mengembalikan `null`
4. Muat dari kunci kosong mengembalikan `null`
5. JSON rusak mengembalikan `null`, tidak melempar
6. `clearPositions` membuat muat berikutnya mengembalikan `null`

Untuk test `localStorage`, buat stub sederhana berbasis `Map` dan pasang ke
`globalThis.localStorage` di `beforeEach`. Jangan pasang jsdom — berlebihan untuk
empat fungsi.

### 1.5 Checkpoint Fase 1

```sh
bun run test
bun run lint
```

Semua test hijau sebelum lanjut. Jangan mulai kerja UI dengan fondasi yang belum
terbukti — kamu tidak akan tahu apakah graf yang salah itu karena fungsi ini atau
karena rendering.

---

## Fase 2 — Shell & Layout Empat Kolom

### 2.1 Cabang lolos-shell di `App.tsx`

Sekarang `App.tsx` merender semua view di dalam struktur yang sama:

```
<aside className="sidebar"> ... </aside>
<div className="main-area">
  <header className="topbar"> judul + subjudul </header>
  <main className="content"> {view} </main>
</div>
```

Dua hal di struktur itu bertabrakan dengan desain baru:

- `.topbar` global akan berduplikasi dengan topbar kontekstual editor
- padding pada `.content` melawan kanvas full-bleed

Solusinya: saat editor agent aktif, `App.tsx` mengambil cabang render berbeda —
tanpa `.topbar`, tanpa padding `.content`, dan sidebar diberi kelas modifier.

`App.tsx` perlu tahu kapan editor aktif. Saat ini state `selectedAgentId` ada di
dalam `AgentsView`. Angkat ke `App.tsx` dan turunkan sebagai props:

```tsx
const [editingAgentId, setEditingAgentId] = useState<string | null>(null)
const isEditingAgent = activeTab === 'agents' && editingAgentId !== null
```

Lalu:

```tsx
<aside className={`sidebar ${isEditingAgent ? 'sidebar--rail' : ''}`}>
```

dan

```tsx
<div className="main-area">
  {!isEditingAgent && <header className="topbar"> ... </header>}
  <main className={`content ${isEditingAgent ? 'content--bleed' : ''}`}>
```

### 2.2 CSS rail di `App.css`

Kuncinya: **jangan ubah JSX sidebar sama sekali**. Cukup satu kelas modifier yang
menyembunyikan dan menciutkan. Ini menjaga agar 6 view lain tidak berisiko rusak.

Yang disembunyikan saat rail aktif: riwayat chat (`.sidebar-chats`), label section
(`.sidebar-section-label`), status pill (`.status-pill`), dan teks label pada item
nav. Yang tetap: ikon nav dan theme toggle.

```css
.sidebar--rail {
  width: 56px;
  padding: 12px 8px;
}

.sidebar--rail .sidebar-section-label,
.sidebar--rail .sidebar-chats,
.sidebar--rail .status-pill,
.sidebar--rail .sidebar-brand-name {
  display: none;
}

.sidebar--rail .sidebar-item {
  justify-content: center;
  padding: 0;
}

/* sembunyikan teks label, sisakan ikon SVG */
.sidebar--rail .sidebar-item { font-size: 0; gap: 0; }
.sidebar--rail .sidebar-item svg { font-size: initial; }

.sidebar--rail .theme-toggle {
  justify-content: center;
  padding: 8px 0;
  font-size: 0;
  gap: 0;
}
.sidebar--rail .theme-toggle svg { font-size: initial; }

.content--bleed {
  padding: 0;
  height: 100svh;
  overflow: hidden;
}
```

**Aksesibilitas — ini wajib, bukan opsional.** Menyembunyikan teks dengan
`font-size: 0` membuat tombol tidak punya nama yang bisa dibaca screen reader. Tiap
`.sidebar-item` harus punya `aria-label` dan `title` saat rail aktif. `title` memberi
tooltip untuk pengguna mouse, `aria-label` memberi nama untuk screen reader. Tambahkan
keduanya di JSX — ini satu-satunya perubahan JSX yang diperbolehkan pada sidebar.

### 2.3 `EditorShell.tsx` — kerangka empat kolom

```tsx
<div className="editor-shell">
  <Palette ... />
  <div className="editor-canvas-area">
    <Topbar ... />
    <Canvas ... />
  </div>
  <InspectorPanel ... />
</div>
```

Rail sudah ditangani `App.tsx`, jadi `EditorShell` hanya mengurus tiga kolom sisanya.

```css
.editor-shell {
  display: flex;
  height: 100svh;
  min-height: 0;
}

.editor-canvas-area {
  position: relative;   /* anchor untuk elemen mengambang */
  flex: 1;
  min-width: 0;         /* WAJIB — tanpa ini kanvas terdorong panel */
  height: 100%;
}
```

`min-width: 0` itu bukan hiasan. Flex item punya `min-width: auto` secara default,
artinya dia menolak menyusut di bawah lebar kontennya. Tanpa baris itu, kanvas akan
mendorong panel kanan keluar layar.

Anggaran lebar pada layar 1440px:

| Kolom | Lebar |
|---|---|
| Rail | 56px |
| Palette | 240px |
| Panel kanan | 360px |
| **Kanvas** | **784px** |

Panel 360px, bukan 320px, karena `ConfigTab` mewarisi form padat dari
`AgentInspector` — pada 320px label dan input mulai saling tindih.

### 2.4 Responsif

```css
@media (max-width: 1280px) {
  /* palette menciut jadi tombol yang membuka popover */
}

@media (max-width: 1024px) {
  /* panel kanan jadi overlay di atas kanvas, bukan kolom */
}
```

Aturannya: kanvas tidak pernah lebih sempit dari ~600px. Di bawah itu editor graf
tidak lagi berguna, dan lebih jujur menampilkan pesan "layar terlalu kecil" daripada
kanvas yang tidak bisa dipakai.

### 2.5 Checkpoint Fase 2

Buka sebuah agent. Yang harus kamu lihat:

- Sidebar menciut jadi rail ikon
- Topbar global hilang
- Tiga kolom kosong memenuhi layar
- Keluar dari editor mengembalikan sidebar dan topbar seperti semula
- Tab lain (Tools, Models, dst) sama sekali tidak berubah
- Tab ke ikon rail dengan keyboard: screen reader membacakan nama tombol

---

## Fase 3 — Kanvas & Kartu Node

### 3.1 Kenapa harus custom node type

Sekarang node dirender lewat `data.label` berisi JSX dengan inline style. Itu
menutup dua pintu: kamu tidak bisa memakai kelas Tailwind, dan kamu tidak bisa
memakai token CSS yang berubah mengikuti tema.

React Flow punya mekanisme resmi untuk ini: `nodeTypes`. Kamu daftarkan komponen,
lalu node dengan `type: 'agentCard'` akan dirender oleh komponen itu.

```tsx
const nodeTypes = { agentCard: AgentNodeCard, exitNode: ExitNode }
```

**Penting:** `nodeTypes` harus didefinisikan **di luar komponen** atau dibungkus
`useMemo` dengan dependency kosong. Kalau objeknya dibuat ulang tiap render, React
Flow akan me-remount semua node tiap render. Ini kesalahan paling umum pada React
Flow dan gejalanya membingungkan: kanvas berkedip dan seleksi hilang sendiri.

### 3.2 Kendala `data` dan cara meredamnya

Custom node React Flow hanya menerima props lewat `data`. Itu berarti setiap kali
kamu mengubah `data`, node akan re-render.

Dua peredam yang wajib ada:

**Bungkus dengan `React.memo`:**

```tsx
export const AgentNodeCard = memo(function AgentNodeCard({ data }: NodeProps) {
  // ...
})
```

**Persempit `data` ke yang benar-benar ditampilkan.** Jangan masukkan seluruh objek
node. Hanya ini:

```ts
interface AgentCardData {
  nodeId: string
  kind: 'agent' | 'tool'
  mode?: 'inline' | 'ref'
  subtitle: string        // label revisi tool/agent, sudah jadi string
  isEntry: boolean
  isSelected: boolean
  errorCount: number
  warningCount: number
}
```

Dengan `memo` + `data` sempit, array node boleh dibangun ulang tiap keystroke. Node
yang isinya tidak berubah tidak akan mengerjakan DOM apa pun, karena `memo`
membandingkan nilai primitifnya dan menemukan semuanya sama.

Kalau kamu memasukkan objek atau fungsi ke `data`, `memo` jadi tidak berguna —
referensinya baru tiap render. Handler jangan lewat `data`; pakai `onNodeClick` di
level `<ReactFlow>`.

### 3.3 Anatomi kartu

Ikuti referensi: ikon, judul, deskripsi terpotong, badge, footer counter.

```
┌────────────────────────────────┐
│ [ikon]  node_id        [ENTRY] │
│ agent (inline)                 │
│ ↳ Rev #3 · low risk            │
│ ─────────────────────────────  │
│ [!] 2 errors                   │
└────────────────────────────────┘
```

Ukuran harus sama dengan `NODE_WIDTH` / `NODE_HEIGHT` di `layout.ts`. Kalau
berbeda, dagre menghitung jarak berdasarkan ukuran yang salah dan kartu akan
bertabrakan. Kalau kamu mengubah ukuran kartu, ubah konstantanya juga.

Warna: **hanya token**. Contoh pemetaan dari hex lama:

| Hex lama | Token |
|---|---|
| `#d97757` | `var(--color-accent)` |
| `#8a867c` | `var(--color-fg-muted)` |
| `#141311` | `var(--color-bg)` |
| `#e5604c`, `#ef8574` | `var(--color-destructive)` |
| `#4cb878`, `#7ed9a2` | `var(--color-success)` |
| `#e0b054` | `var(--color-warning)` |

Daftar token yang tersedia (didefinisikan di `index.css`, dengan nilai berbeda untuk
light dan `.dark`):

```
--color-bg              --color-fg              --color-accent
--color-bg-sidebar      --color-fg-muted        --color-accent-fg
--color-bg-elevated     --color-fg-subtle       --color-accent-muted
--color-bg-muted

--color-success         --color-border          --color-terminal
--color-warning         --color-border-strong   --color-terminal-fg
--color-destructive     --color-ring
```

Perhatikan namanya `--color-destructive`, bukan `--color-danger`. Tidak ada
`--color-accent-soft`. Kalau kamu butuh warna yang belum ada, tambahkan token di
**kedua** blok (light dan `.dark`) — kalau hanya salah satu, tema yang lain akan
mewarisi nilai yang salah.

Kartu butuh `<Handle type="target" position={Position.Top} />` dan
`<Handle type="source" position={Position.Bottom} />` supaya tarik-koneksi bisa
bekerja di Fase 4.

### 3.4 `ExitNode.tsx`

Named exit digambar sebagai pil, bukan kartu. Node ini tidak ada di
`document.nodes` — dia sintetis, dibuat dari `document.named_exits`. ID-nya
`__exit_<nama>`, sesuai yang dihasilkan `extractEdgePairs`.

Exit node tidak bisa dipilih dan tidak punya config. Set
`selectable={false}` pada node-nya.

### 3.5 `Canvas.tsx`

Tanggung jawabnya:

1. Bangun array node React Flow dari `document.nodes` + `named_exits`
2. Bangun array edge dari `extractEdgePairs`
3. Ambil posisi: `loadPositions()` dulu, kalau `null` jalankan `runLayout()`
4. Simpan posisi saat user selesai drag (`onNodeDragStop`)
5. Render group dari `deriveGroups()`
6. Legend + kontrol zoom mengambang

Gaya edge — pertahankan yang sudah ada, dashed dengan `MarkerType.ArrowClosed`,
tapi ganti hex jadi token. Bedakan per kind:

| kind | warna | tebal |
|---|---|---|
| `direct`, `join` | `--color-fg-muted` | 2 |
| `exit` | `--color-success` | 2 |
| `semantic` | `--color-accent` | 2 |
| `mechanical` | `--color-warning` | 2 |
| fallback (`default`/`else`) | sama dengan induknya | 1, dash lebih rapat |
| ada error | `--color-destructive` | 3 |

Karena React Flow memakai inline style untuk stroke, kamu perlu membaca nilai token
lewat `getComputedStyle(document.documentElement).getPropertyValue('--color-accent')`
sekali di dalam `useEffect` yang bergantung pada tema, lalu menyimpannya di state.
Jangan panggil `getComputedStyle` di dalam loop render — itu memaksa layout reflow
tiap edge.

Nilai token di repo ini ditulis dalam format `oklch(...)`. `getComputedStyle` akan
mengembalikan string `oklch(...)` apa adanya, dan itu valid sebagai nilai `stroke`
SVG di browser modern. Kamu tidak perlu mengonversinya.

### 3.6 `ParallelGroup.tsx`

Div ber-`position: absolute` di belakang node, di dalam viewport React Flow. Border
putus-putus, latar sangat transparan, label kecil huruf kapital di pojok kiri atas.

Supaya ikut ter-pan dan ter-zoom bersama kanvas, render di dalam
`<ViewportPortal>` dari `@xyflow/react`. Kalau kamu render di luar itu, group akan
diam di tempat sementara node bergerak — kelihatan jelas salah begitu kamu pan.

`z-index` harus di bawah node.

### 3.7 Tombol Reset layout

Letakkan di kontrol kanvas. Memanggil `clearPositions(agentId)` lalu memaksa
`runLayout` jalan lagi. Ini jalan keluar kalau user mengacak posisi dan ingin
kembali rapi tanpa harus mengubah himpunan node.

### 3.8 Checkpoint Fase 3

- Node muncul dalam susunan hierarkis, bukan grid tiga kolom
- Kartu memakai warna tema; ganti ke light mode dan kanvas ikut terang
- Drag node lalu refresh halaman: posisi bertahan
- Tambah node lalu refresh: seluruh layout dihitung ulang (ini benar, sesuai
  keputusan 5)
- Klik Reset layout mengembalikan susunan dagre
- Group `PARALLEL FLOW` muncul pada agent yang punya edge `join`
- Pan dan zoom: group ikut bergerak bersama node

---

## Fase 4 — Topbar, Save, Dirty Tracking, Konflik

### 4.1 `useAgentEditor.ts` — reducer

`AgentsView` sekarang punya belasan `useState` terpisah. Itu sumber bug, karena
aksi-aksinya saling terkait: memilih node harus membersihkan seleksi edge; save harus
menyetel versi, diagnostics, dan status dirty sekaligus. Dengan `useState` terpisah,
gampang lupa salah satu.

```ts
export interface EditorState {
  doc: Record<string, any> | null
  /** salinan dokumen sesaat setelah save terakhir — untuk dirty tracking */
  savedDoc: string | null
  version: number
  diagnostics: Diagnostic[]
  selection:
    | { type: 'none' }
    | { type: 'node'; nodeId: string }
    | { type: 'edge'; edgeIndex: number }
  saving: boolean
  publishing: boolean
  conflict: { currentVersion: number; currentDraft: Record<string, any> } | null
  status: string | null
}

export type EditorAction =
  | { type: 'loaded'; doc: Record<string, any>; version: number; diagnostics: Diagnostic[] }
  | { type: 'docChanged'; doc: Record<string, any> }
  | { type: 'selectNode'; nodeId: string }
  | { type: 'selectEdge'; edgeIndex: number }
  | { type: 'clearSelection' }
  | { type: 'saveStart' }
  | { type: 'saveSuccess'; doc: Record<string, any>; version: number; diagnostics: Diagnostic[] }
  | { type: 'saveConflict'; currentVersion: number; currentDraft: Record<string, any> }
  | { type: 'saveError'; message: string }
  | { type: 'dismissConflict' }
  // ... aksi publish serupa
```

Dirty tracking cukup begini:

```ts
const isDirty = state.savedDoc !== null
  && JSON.stringify(state.doc) !== state.savedDoc
```

`JSON.stringify` untuk perbandingan memang terkesan kasar, tapi dokumen agent
ukurannya kilobyte, bukan megabyte, dan ini hanya jalan sekali per render. Jangan
pasang pustaka deep-equal untuk ini.

Satu hal yang harus kamu waspadai: urutan kunci objek mempengaruhi hasil
`JSON.stringify`. Selama kamu selalu memperbarui dokumen dengan spread
(`{ ...doc, nodes: [...] }`), urutan kunci terjaga dan ini aman. Kalau suatu saat
dirty muncul padahal tidak ada perubahan, kemungkinan besar ada kode yang membangun
ulang objek dokumen dari nol dengan urutan kunci berbeda.

### 4.2 `Topbar.tsx`

Mengambang di atas kanvas, `position: absolute; top: 0; left: 0; right: 0`. Latar
semi-transparan dengan `backdrop-filter: blur(8px)` supaya kanvas di belakangnya
masih terasa.

Isinya kiri ke kanan:

1. Tombol kembali (panah + "All agents") — ini satu-satunya jalan keluar dari editor
2. Nama agent
3. Badge `Unsaved changes` (hanya saat `isDirty`)
4. Badge `Draft v{n}`
5. Badge `Published rev #{n}` (kalau ada `active_revision_id`)
6. Spacer
7. Kontrol zoom, tombol Reset layout
8. Tombol `Save draft`
9. Tombol `Publish` (primary)

Pesan status (`state.status`) tampil sebagai toast singkat, bukan banner permanen.
Banner permanen mendorong layout dan itu tidak bisa diterima pada kanvas full-bleed.

### 4.3 Menangani 409

Backend memakai optimistic locking. `PUT /agents/{id}/draft` mengirim `version`; kalau
versi itu bukan versi terkini, backend menolak dengan 409 dan body berisi
`current_version` serta `current_draft`.

Sekarang `api.ts` membuang informasi itu — lihat `updateAgentDraft`, dia melakukan
`JSON.stringify(err.detail)` dan melemparnya sebagai pesan teks. Tidak ada jalan
pemulihan dari situ.

Perbaiki dengan kelas error khusus:

```ts
export class DraftConflictError extends Error {
  constructor(
    public currentVersion: number,
    public currentDraft: Record<string, any>
  ) {
    super('Draft conflict')
    this.name = 'DraftConflictError'
  }
}
```

Di `updateAgentDraft`, periksa `res.status === 409` dan lempar `DraftConflictError`
dengan field dari body. Error lain tetap seperti sekarang.

Dialognya dua tombol, tidak ada merge otomatis:

> **Draft ini diubah di tempat lain**
> Ada versi yang lebih baru (v{currentVersion}) di server.
>
> [Muat ulang dan buang perubahan saya]  [Timpa dengan versi saya]

- **Muat ulang** → `dispatch({ type: 'loaded', doc: currentDraft, version: currentVersion, ... })`
- **Timpa** → panggil `updateAgentDraft` lagi dengan `currentVersion`

Menggabungkan dua topologi graf secara otomatis bisa menghasilkan dokumen yang tidak
dimaksudkan siapa pun. Dua pilihan eksplisit lebih jujur.

### 4.4 Publish

`POST /agents/{id}/publish` mengembalikan 422 dengan `detail.diagnostics[]` kalau
dokumen tidak valid. Diagnostics itu harus masuk ke state supaya badge di kartu node
dan daftar diagnostics langsung menunjukkan apa yang salah — bukan cuma muncul
sebagai pesan error yang hilang dalam tiga detik.

### 4.5 Checkpoint Fase 4

- Ubah sesuatu → badge `Unsaved changes` muncul
- Save → badge hilang, nomor versi naik
- Buka agent yang sama di dua tab, save di keduanya → tab kedua menampilkan dialog
  konflik, dan kedua tombol benar-benar bekerja
- Publish dengan dokumen invalid → diagnostics muncul di kanvas, bukan hanya toast

---

## Fase 5 — Panel Kanan Bertab

### 5.1 Tiga konteks

Panel menampilkan hal berbeda tergantung `state.selection`:

| Seleksi | Isi panel |
|---|---|
| `node` | Tab Config berisi config node itu |
| `edge` | `EdgeEditor` untuk edge itu |
| `none` | `TopologyForm`: `entry_node_id`, `named_exits`, `recursion_limit` |

Tab Test dan History selalu tersedia, tidak bergantung seleksi.

### 5.2 `ConfigTab.tsx`

Dipindah dari `AgentInspector.tsx` (658 baris). Isinya accordion: prompt, model,
limits, dynamic context, long-context handling. Logikanya sudah benar — kamu
memindahkan, bukan menulis ulang.

Yang berubah: dia menerima node yang sedang dipilih, bukan selalu root node.

Hati-hati dengan dua objek yang punya allowlist ketat di backend
(`backend/src/orchestrator/domain/validation.py`):

- `context_policy`: hanya `memory`, `knowledge_top_k`, `upstream`, `max_chars`,
  `max_item_chars`
- `middleware_policy`: hanya `summarization`, `context_editing`, `pii`,
  `tool_approval`, `tool_selection`, `model_call_limit`, `tool_call_limit`,
  `model_retry`

Kunci di luar daftar itu akan ditolak saat publish. Node sendiri tidak punya
allowlist — field tak dikenal pada node tidak ditolak validator.

### 5.3 `EdgeEditor.tsx`

Ini bagian paling rumit di panel, karena lima kind punya bentuk berbeda total.

Alurnya: user memilih kind lewat dropdown, lalu form menyesuaikan.

| kind | field form |
|---|---|
| `direct` | source (select node), target (select node) |
| `exit` | source, result_name (select dari `named_exits`) |
| `join` | sources (multi-select), target, mode join |
| `semantic` | source node, source field, tabel route (nilai → target), default |
| `mechanical` | source node, source field, operator, value, then, else |

Ingat lagi: untuk `semantic` dan `mechanical`, `source` disimpan sebagai array
`[nodeId, fieldName]`. Form punya dua input terpisah, tapi keduanya menulis ke satu
array. Ini sumber bug kalau kamu lupa.

Ada helper `parseFieldPathMapping` di `GraphCanvas.tsx` sekitar baris 335 yang
menangani parsing ini. Baca dulu, pindahkan, jangan tulis ulang dari nol.

### 5.4 Membuat edge dengan tarik-koneksi

Handler `onConnect` React Flow menghasilkan `{ source, target }`. Buat edge `direct`
sebagai default:

```ts
const onConnect = useCallback((conn: Connection) => {
  if (!conn.source || !conn.target) return
  dispatch({ type: 'docChanged', doc: {
    ...doc,
    edges: [...doc.edges, { kind: 'direct', source: conn.source, target: conn.target }],
  }})
  dispatch({ type: 'selectEdge', edgeIndex: doc.edges.length })
}, [doc])
```

Setelah dibuat, edge langsung terpilih dan panel membuka `EdgeEditor` — dari sana
user bisa mengubah kind-nya jadi `semantic` atau lainnya. Tarik-koneksi hanya
memodelkan satu pasang (sumber, target); percabangan perlu form.

Tolak koneksi ke diri sendiri dan koneksi yang sudah ada.

### 5.5 `TestTab.tsx`

Dipindah dari run panel `AgentsView`. Memakai `POST /agents/{id}/runs` (SSE),
`GET /runs/{id}`, `GET /runs/{id}/interrupts`, `POST /runs/{id}/resume`.

Agent yang belum punya `active_revision_id` tidak bisa dijalankan. Tampilkan pesan
yang mengarahkan user ke tombol Publish, jangan cuma disable tombolnya tanpa
penjelasan.

### 5.6 `HistoryTab.tsx`

Memakai `GET /agents/{id}/revisions`. **Method ini belum ada di `api.ts`** — kamu
harus menambahkannya. Ikuti pola method lain di file itu:

```ts
async listAgentRevisions(agentId: string): Promise<AgentRevision[]> {
  const res = await fetch(`/api/v1/agents/${agentId}/revisions`)
  if (!res.ok) throw new Error('Failed to fetch revisions')
  return res.json()
},
```

Tipe `AgentRevision` sudah ada di `api.ts`.

Tiap baris menampilkan nomor revisi, waktu dibuat, dan `content_hash` dipotong 10
karakter. Tandai revisi yang sedang aktif.

### 5.7 Checkpoint Fase 5

- Klik node → panel menampilkan config node itu
- Klik edge → panel menampilkan editor edge
- Klik kanvas kosong → panel menampilkan topology settings
- Tarik dari handle bawah satu node ke handle atas node lain → edge `direct` dibuat
  dan langsung terpilih
- Ubah kind edge jadi `semantic`, isi route → kanvas menggambar satu edge per route
- Tab Test menjalankan agent yang sudah dipublish
- Tab History menampilkan daftar revisi

---

## Fase 6 — Palette

Kolom kedua, 240px. Isinya:

1. Kotak search
2. Tombol "New inline node"
3. Daftar agent lain di workspace (dari `api.listAgents()`), bisa dicari

Item agent bisa di-drag ke kanvas untuk membuat node mode `ref` dengan
`agent_revision_id` terisi. Implementasinya pakai HTML5 drag-and-drop:

```tsx
// di item palette
onDragStart={(e) => {
  e.dataTransfer.setData('application/reactflow', JSON.stringify({
    kind: 'agent', mode: 'ref', agentRevisionId: rev.id,
  }))
  e.dataTransfer.effectAllowed = 'move'
}}
```

```tsx
// di wrapper kanvas
onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }}
onDrop={(e) => {
  e.preventDefault()
  const raw = e.dataTransfer.getData('application/reactflow')
  if (!raw) return
  // ... tambahkan node ke dokumen
}}
```

Node baru butuh ID yang valid: regex backend `^[a-z][a-z0-9_]{0,63}$`. Turunkan dari
nama agent — huruf kecil, spasi jadi underscore, buang karakter lain. Kalau bentrok
dengan ID yang sudah ada, tambahkan akhiran angka.

Posisi drop diabaikan. Ingat keputusan 5: menambah node mengubah himpunan node,
yang membuang cache dan memicu layout ulang penuh. Jadi node akan ditempatkan dagre,
bukan di titik jatuhnya kursor. Ini konsisten dan disengaja — jangan "perbaiki"
dengan mempertahankan posisi drop, itu akan membuat perilaku tidak konsisten dengan
cara lain menambah node.

Jangan tampilkan agent yang sedang diedit di daftar — agent tidak bisa mereferensi
dirinya sendiri.

---

## Fase 7 — Diagnostics

### 7.1 Memetakan diagnostic ke node

`Diagnostic` bentuknya:

```ts
interface Diagnostic {
  code: string
  path: string          // perhatikan: `path`, bukan `instance_path`
  message: string
  severity: 'error' | 'warning'
}
```

`path` berisi pointer seperti `/nodes/3/model` atau `/edges/1/routes`. Logika
pemetaan ini sudah ada di `GraphCanvas.tsx` — cari `nodeDiagMap` dan `edgeDiagMap`.
Pindahkan, jangan tulis ulang.

Polanya: regex `/nodes\/(\d+)/` dan `/edges\/(\d+)/`, ambil angkanya sebagai index ke
array. Diagnostic yang tidak cocok keduanya adalah diagnostic tingkat dokumen.

### 7.2 Dua lapis tampilan

**Badge di kartu node.** Hitung error dan warning per node, tampilkan di footer
kartu. Klik kartu memilih node itu dan membuka panel Config. Ini yang menjawab
pertanyaan "node yang mana".

**Daftar mengambang.** Panel yang bisa diciutkan di bawah kanvas, berisi semua
diagnostic termasuk yang tingkat dokumen. Ini yang menjawab pertanyaan "berapa banyak
lagi yang harus diperbaiki" — penting karena publish gagal dengan daftar lengkap.

Klik item di daftar memilih node atau edge terkait dan menggeser viewport ke sana
(`reactFlowInstance.setCenter`).

Panel default-nya terbuka kalau ada error, tertutup kalau hanya warning.

---

## Fase 8 — Pembersihan

### 8.1 Hapus `GraphCanvas.tsx`

Seluruhnya. Semua fungsinya sudah pindah:

| Bagian lama | Tujuan baru |
|---|---|
| Tab Visual | `Canvas.tsx` |
| Tab Nodes | Palette + panel Config |
| Tab Edges | `EdgeEditor.tsx` |
| Tab Topology Settings | `TopologyForm.tsx` |
| `rfEdges` | `lib/edges.ts` |
| `nodeDiagMap` / `edgeDiagMap` | Fase 7 |
| `parseFieldPathMapping` | `EdgeEditor.tsx` |

Sebelum menghapus, `grep` nama komponennya di seluruh `src/` untuk memastikan tidak
ada yang masih mengimpor.

### 8.2 Rampingkan `AgentsView.tsx`

Sisakan mode list: grid kartu, search, tombol buat agent baru. Klik kartu memanggil
`onEditAgent(id)` yang diteruskan dari `App.tsx`.

Semua state editor (`draftDoc`, `draftVersion`, `diagnostics`, `savingDraft`,
`publishing`, `saveStatus`, `runLogs`, `runOutput`, `runId`, `pendingInterrupt`,
`runStatus`) dihapus dari sini — sudah pindah ke reducer `EditorShell`.

Tampilan kartu di mode list **tidak diubah**. Itu di luar cakupan.

### 8.3 Hapus `AgentInspector.tsx`

Setelah isinya pindah ke `ConfigTab.tsx`.

### 8.4 Bersihkan CSS mati

Kelas-kelas ini kemungkinan jadi yatim: `.editor-page`, `.editor-topbar`,
`.editor-topbar-copy`, `.diagnostics-panel`, `.diagnostic-item`, `.run-panel`,
`.run-header`, `.run-inputs`, `.status-banner`.

Periksa satu per satu dengan `grep` sebelum menghapus. Beberapa mungkin dipakai view
lain.

### 8.5 Checkpoint akhir

```sh
bun run lint
bun run test
bun run build
```

Ketiganya harus bersih. `bun run build` menjalankan `tsc -b` lebih dulu, jadi error
TypeScript akan tertangkap di situ.

Uji manual menyeluruh:

- [ ] Buat agent baru, tambah node, hubungkan, save, publish
- [ ] Kelima jenis edge bisa dibuat dan digambar benar di kanvas
- [ ] Drag node, refresh, posisi bertahan
- [ ] Tambah node, layout dihitung ulang
- [ ] Reset layout bekerja
- [ ] Group `PARALLEL FLOW` muncul pada graf dengan `join`
- [ ] Dialog konflik muncul dan kedua tombolnya bekerja
- [ ] Diagnostics muncul sebagai badge dan daftar
- [ ] Light dan dark mode keduanya benar — tidak ada hex tersisa
- [ ] Rail ikon bisa dinavigasi keyboard, punya nama yang terbaca
- [ ] Tab Tools, Models, Knowledge, Credentials, Conversations tidak berubah

---

## Lampiran A — Bentuk Dokumen Agent

Aturan yang divalidasi backend:

- `schema_version` harus `1`
- Node ID cocok dengan `^[a-z][a-z0-9_]{0,63}$`
- `recursion_limit` antara 1 dan 100

Kerangka:

```jsonc
{
  "schema_version": 1,
  "entry_node_id": "planner",
  "named_exits": ["success", "failed"],
  "recursion_limit": 25,
  "nodes": [
    {
      "id": "planner",
      "kind": "agent",
      "agent": { "mode": "inline", "model_revision_id": "..." },
      "context_policy": { "memory": true, "knowledge_top_k": 5 },
      "middleware_policy": { "tool_approval": { /* ... */ } }
    },
    {
      "id": "search",
      "kind": "tool",
      "tool_revision_id": "..."
    }
  ],
  "edges": [
    { "kind": "direct", "source": "planner", "target": "search" },

    { "kind": "exit", "source": "search", "result_name": "success" },

    { "kind": "join", "sources": ["a", "b", "c"], "target": "merge", "join": "all" },

    {
      "kind": "semantic",
      "source": ["classifier", "category"],   // [nodeId, fieldName] — ARRAY
      "routes": { "billing": "billing_agent", "tech": "tech_agent" },
      "default": "fallback_agent"
    },

    {
      "kind": "mechanical",
      "source": ["scorer", "confidence"],     // [nodeId, fieldName] — ARRAY
      "operator": ">=",
      "value": 0.8,
      "then": "auto_approve",
      "else": "human_review"
    }
  ]
}
```

Sekali lagi, karena ini sumber bug paling sering: `source` pada `semantic` dan
`mechanical` adalah **array**, sementara pada `direct` dan `exit` adalah **string**.

---

## Lampiran B — Endpoint Backend

Semua sudah ada. Tidak ada yang perlu ditambahkan di backend.

| Method | Path | Dipakai untuk |
|---|---|---|
| GET | `/api/v1/agents` | List agent, palette |
| GET | `/api/v1/agents/{id}` | Muat editor |
| GET | `/api/v1/agents/{id}/draft` | Muat draft |
| PUT | `/api/v1/agents/{id}/draft` | Save — 409 `draft_conflict` |
| POST | `/api/v1/agents/{id}/publish` | Publish — 422 `publish_validation_failed` |
| GET | `/api/v1/agents/{id}/revisions` | Tab History — **tambahkan ke `api.ts`** |
| POST | `/api/v1/agents/{id}/runs` | Tab Test (SSE) |
| GET | `/api/v1/runs/{id}` | Status run |
| GET | `/api/v1/runs/{id}/interrupts` | Approval tertunda |
| POST | `/api/v1/runs/{id}/resume` | Lanjutkan setelah approval |

Body 409:

```json
{
  "detail": {
    "code": "draft_conflict",
    "current_version": 7,
    "current_draft": { /* dokumen */ }
  }
}
```

Body 422 saat publish:

```json
{
  "detail": {
    "code": "publish_validation_failed",
    "diagnostics": [
      { "code": "...", "path": "/nodes/2/model", "message": "...", "severity": "error" }
    ]
  }
}
```

---

## Kalau Kamu Tersangkut

Urutan yang saya sarankan:

1. Baca ulang bagian yang relevan di dokumen ini — kebanyakan jebakan sudah ditulis
2. Baca kode lama di `GraphCanvas.tsx`; banyak logika yang kamu butuhkan sudah ada di
   sana dan sudah benar, tinggal dipindah
3. Kalau kanvas berkedip atau seleksi hilang sendiri: hampir pasti `nodeTypes` dibuat
   ulang tiap render
4. Kalau node bertabrakan: `NODE_WIDTH` / `NODE_HEIGHT` tidak cocok dengan ukuran
   kartu sebenarnya
5. Kalau node bergeser dari edge-nya: lupa konversi pusat → pojok kiri atas
6. Kalau edge menunjuk ke tempat aneh pada `semantic`/`mechanical`: `source`
   diperlakukan sebagai string padahal array

Kalau sesuatu di dokumen ini bertentangan dengan apa yang kamu lihat di kode,
**kodenya yang benar** — dan beri tahu, supaya dokumen ini diperbaiki.
