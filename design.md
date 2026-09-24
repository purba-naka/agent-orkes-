# Design System: Agent Orchestrator

**Status:** Aktif — Source of Truth untuk seluruh UI
**Tanggal:** 17 September 2026
**Versi:** 1.0
**Referensi desain:** Claude (Anthropic) + ChatGPT (OpenAI)
**Stack:** React 19 + Vite + Tailwind CSS v4 (CSS-first, token via `@theme`)

---

## 1. Filosofi Desain

> **"Warm Intelligence"** — tenang, hangat, dan fokus pada percakapan.

Produk ini adalah *agent orchestration studio*: bagian developer tool (agents, tools, knowledge) dan bagian conversational (chat dengan streaming + HITL). Desain mengambil yang terbaik dari dua referensi:

| Aspek | Dari Claude | Dari ChatGPT |
|---|---|---|
| Palet | Warm ivory/oat neutrals, terracotta accent | Dark mode yang nyaman, netral |
| Layout | Sidebar lebar, whitespace murah hati | Struktur chat terpusat, minimal chrome |
| Tipografi | Serif display untuk judul & sapaan | Sans bersih, hirarki jelas |
| Chat | Message flat tanpa bubble, feeling "dokumen" | Composer pill, streaming mulus |

**Prinsip:**

1. **Content-first** — chrome UI mundur; konten (pesan, konfigurasi, graf) maju.
2. **Calm, not loud** — tidak ada warna neon, tidak ada gradient mencolok. Warna hanya untuk status dan aksi.
3. **Satu aksen** — terracotta adalah satu-satunya warna brand. Semua lainnya netral hangat.
4. **Motion bermakna** — animasi menjelaskan perubahan state (streaming, loading, hover), bukan dekorasi.
5. **Agent adalah data** — UI memperlakukan konfigurasi sebagai dokumen yang tenang dan mudah dibaca, bukan dashboard yang ramai.

**Anti-pattern (hindari):** emoji sebagai ikon, bubble chat untuk assistant, gradient banner, warna biru default Tailwind, animasi > 300ms, kontras < 4.5:1, spinner > 2 detik untuk streaming.

---

## 2. Token Warna (OKLCH)

Didefinisikan di `frontend/src/index.css` dalam blok `@theme` (Tailwind v4 CSS-first). Mode gelap meng-override token yang sama di selector `.dark`.

### 2.1 Light Mode (default) — "Ivory"

| Token | Nilai | ~Hex | Penggunaan |
|---|---|---|---|
| `--color-bg` | `oklch(97.6% 0.006 85)` | `#faf9f5` | Background utama (canvas chat, area konten) |
| `--color-bg-sidebar` | `oklch(95.3% 0.008 85)` | `#f0eee6` | Sidebar (oat, ala Claude) |
| `--color-bg-elevated` | `oklch(99% 0.003 85)` | `#fefdf9` | Card, modal, popover |
| `--color-bg-muted` | `oklch(94% 0.007 85)` | `#ebe9e1` | Hover row, code background, well |
| `--color-fg` | `oklch(24% 0.01 80)` | `#30302c` | Teks utama |
| `--color-fg-muted` | `oklch(48% 0.012 80)` | `#6e6b63` | Teks sekunder, label |
| `--color-fg-subtle` | `oklch(58% 0.01 80)` | `#8b8778` | Timestamp, hint |
| `--color-accent` | `oklch(64% 0.115 45)` | `#d97757` | Aksi primer, link, focus ring (terracotta Claude) |
| `--color-accent-fg` | `oklch(99% 0.003 85)` | `#fefdf9` | Teks di atas accent |
| `--color-accent-muted` | `oklch(93% 0.03 50)` | `#f6e5dd` | Background hover accent, badge terpilih |
| `--color-success` | `oklch(58% 0.13 155)` | `#2f9e63` | Status online/connected/published |
| `--color-warning` | `oklch(70% 0.13 75)` | `#d8a03d` | Warning diagnostik, risk medium |
| `--color-destructive` | `oklch(55% 0.19 27)` | `#d4442e` | Error, danger, risk high |
| `--color-border` | `oklch(89% 0.008 85)` | `#e0ddd2` | Border default |
| `--color-border-strong` | `oklch(82% 0.01 85)` | `#ccc8ba` | Border hover, elemen aktif |
| `--color-ring` | `oklch(64% 0.115 45)` | `#d97757` | Focus ring |

### 2.2 Dark Mode — "Warm Charcoal"

Diaktifkan via class `.dark` pada `<html>` (toggle manual, default mengikuti `prefers-color-scheme`).

| Token | Nilai | ~Hex | Catatan |
|---|---|---|---|
| `--color-bg` | `oklch(20.5% 0.005 80)` | `#262624` | Charcoal hangat (bukan abu dingin ala #212121) |
| `--color-bg-sidebar` | `oklch(17.5% 0.005 80)` | `#1f1f1e` | Sidebar sedikit lebih gelap |
| `--color-bg-elevated` | `oklch(24% 0.006 80)` | `#2e2d2a` | Card, modal |
| `--color-bg-muted` | `oklch(27% 0.006 80)` | `#36342f` | Hover, well |
| `--color-fg` | `oklch(93% 0.005 85)` | `#ece9e2` | Kontras ~13:1 |
| `--color-fg-muted` | `oklch(72% 0.008 80)` | `#b0aba0` | Kontras ~6.5:1 |
| `--color-fg-subtle` | `oklch(58% 0.008 80)` | `#8a867c` | Kontras ~4.6:1 |
| `--color-accent` | `oklch(71% 0.105 45)` | `#e08a68` | Terracotta lebih terang untuk kontras dark |
| `--color-accent-fg` | `oklch(20.5% 0.005 80)` | `#262624` | Teks di atas accent (dark) |
| `--color-accent-muted` | `oklch(30% 0.04 45)` | `#4a332a` | Hover accent dark |
| `--color-success` | `oklch(70% 0.13 155)` | `#4cb878` | |
| `--color-warning` | `oklch(76% 0.12 75)` | `#e0b054` | |
| `--color-destructive` | `oklch(65% 0.17 27)` | `#e5604c` | |
| `--color-border` | `oklch(29% 0.006 80)` | `#3a3833` | |
| `--color-border-strong` | `oklch(38% 0.007 80)` | `#4c4a43` | |
| `--color-ring` | `oklch(71% 0.105 45)` | `#e08a68` | |

**Aturan penggunaan warna:**

- Warna **tidak pernah** menjadi satu-satunya pembawa makna (pair dengan label/ikon).
- Status pill selalu: dot + teks, bukan blok warna penuh.
- `destructive` hanya untuk aksi destruktif dan error — bukan untuk semua "secondary danger".
- Tidak ada hex mentah di komponen; semua lewat token.

---

## 3. Tipografi

Tiga keluarga font, semuanya self-hosted via Fontsource (tanpa blocking request eksternal):

| Role | Font | Paket | Penggunaan |
|---|---|---|---|
| **Sans (UI)** | Inter Variable | `@fontsource-variable/inter` | Semua UI: body, form, table, button, nav |
| **Serif (Display)** | Source Serif 4 Variable | `@fontsource-variable/source-serif-4` | Judul halaman, empty state, sapaan chat ("Good evening"), heading card utama |
| **Mono** | JetBrains Mono Variable | `@fontsource-variable/jetbrains-mono` | Code, terminal, ID, `code` inline, JSON payload |

### Skala

| Token | Ukuran | Line-height | Font | Contoh |
|---|---|---|---|---|
| `--text-display` | 30px / 1.2 | Serif 600 | Judul halaman, sapaan |
| `--text-title` | 20px / 1.3 | Sans 600 (Serif untuk card besar) | Judul section, nama agent |
| `--text-body` | 15px / 1.6 | Sans 400 | Default body & chat |
| `--text-body-lg` | 16px / 1.65 | Sans 400 | Isi pesan chat |
| `--text-secondary` | 13.5px / 1.5 | Sans 400 | Label, meta, helper |
| `--text-caption` | 12px / 1.4 | Sans 500 | Badge, timestamp, eyebrow |
| `--text-code` | 13px / 1.6 | Mono 400 | Terminal, code block |

Base font-size: **15px** (density tool); chat thread 16px untuk kenyamanan membaca panjang.

Aturan: body minimum 12px; line-height body 1.5–1.65; letter-spacing heading -0.01em; jangan pakai font-weight > 600.

---

## 4. Spacing, Radius, Elevation

### Spacing (skala 4pt)

```
--space-1: 4px    --space-5: 20px   --space-10: 40px
--space-2: 8px    --space-6: 24px   --space-12: 48px
--space-3: 12px   --space-8: 32px   --space-16: 64px
--space-4: 16px
```

Konten dashboard max-width `1200px`, padding horizontal `24–32px`. Thread chat terpusat max-width `768px`.

### Radius

| Token | Nilai | Untuk |
|---|---|---|
| `--radius-sm` | 6px | Badge, chip, input kecil |
| `--radius-md` | 8px | Button, input, table |
| `--radius-lg` | 12px | Card, panel, modal |
| `--radius-xl` | 16px | Composer chat, panel besar |
| `--radius-full` | 9999px | Dot status, avatar, pill |

### Elevation (bayangan sangat halus — flat-first)

| Token | Nilai | Untuk |
|---|---|---|
| `--shadow-sm` | `0 1px 2px rgba(30,25,20,0.05)` | Card default |
| `--shadow-md` | `0 2px 8px rgba(30,25,20,0.08)` | Hover card, composer focus |
| `--shadow-lg` | `0 8px 24px rgba(30,25,20,0.14)` | Modal, dropdown, popover |

Dark mode: shadow dikali opasitas lebih tinggi. **Tidak ada** bayangan berlapis tebal; card utamanya dibedakan dengan border + bg elevated.

---

## 5. Struktur Layout — App Shell

```
┌──────────┬────────────────────────────────────────────┐
│          │  Topbar: judul halaman + aksi kontekstual  │
│ Sidebar  ├────────────────────────────────────────────┤
│ 264px    │                                            │
│          │   Konten (max 1200px)                      │
│ • Chat   │   - Chat: thread terpusat 768px            │
│ • Agents │     + composer pill di bawah               │
│ • Tools  │   - Lain: grid/tabel/panel                 │
│ • Knowl. │                                            │
│ • Models │                                            │
│ • Creds  │                                            │
│ ──────── │                                            │
│ Status   │                                            │
│ API • DB │                                            │
│ Theme ◐  │                                            │
└──────────┴────────────────────────────────────────────┘
```

- **Sidebar 264px, fixed, full-height.** Bagian atas: logo + nama produk + versi. Tengah: navigasi utama. Bawah: status sistem (dot API & DB) + toggle theme. Ini pola yang identik di ChatGPT dan Claude.
- **Nav item:** ikon Lucide 18px + label, tinggi 36px, radius 8px. State aktif: bg `accent-muted` + teks accent; state hover: bg muted.
- **Topbar 56px:** judul halaman (Serif di halaman besar) + breadcrumb ringan + aksi kontekstual kanan.
- **Mobile (<900px):** sidebar menjadi drawer overlay (hamburger di topbar), konten full-width.

---

## 6. Komponen Inti

### 6.1 Button

| Variant | Style | Pakai untuk |
|---|---|---|
| `primary` | bg accent, teks accent-fg, radius-md, shadow-sm | Satu aksi utama per view (Send, Publish, Approve) |
| `secondary` | bg transparent, border border-strong, teks fg | Aksi pendamping (Cancel, Edit) |
| `ghost` | bg transparent, teks fg-muted, hover bg muted | Aksi tersier, toolbar ikon |
| `danger` | bg destructive, teks putih | Hapus, Reject |

- Tinggi 36px (default) / 30px (`small`) / 44px (mobile touch target).
- Padding horizontal 14px; gap ikon-teks 8px; transition 150ms.
- Focus: ring 2px offset 2px. Disabled: opacity 0.5, `cursor: not-allowed`.
- **Dilarang**: gradient, shadow berat, radius penuh (kecuali composer send).

### 6.2 Chat — pola paling penting (ala ChatGPT/Claude)

- **Assistant: TANPA bubble.** Pesan assistant adalah blok teks flat, lebar penuh kolom, dipisah whitespace 24px — terasa seperti dokumen (pola Claude). Avatar lingkaran 28px (ikon Bot).
- **User: subtle bubble.** bg `bg-muted`, radius-lg (12px), max-width 85%, rata kanan. Tanpa border tebal, tanpa warna biru.
- **Metadata pesan** (role, seq, waktu): caption 12px `fg-subtle`, di atas isi pesan — bukan di dalam border.
- **Streaming:** teks token muncul langsung; caret berkedip (blok 2px tinggi teks, animasi 1s step-end) di ujung; label "Assistant (streaming…)" diganti dot pulse. **Tidak ada spinner penuh** saat menunggu token pertama — tampilkan 3-dot pulse.
- **Composer:** pill radius-xl (16px), border border-strong, bg elevated, shadow-md saat focus-within. Textarea auto-grow 1–6 baris. Tombol Send ikon panah, lingkaran, aktif hanya saat ada teks. Hint kecil di bawah ("Enter untuk kirim, Shift+Enter baris baru" bila multi-line didukung).
- **HITL interrupt card:** panel dengan border accent 1px + bg accent-muted tipis, ikon ShieldAlert, payload JSON dalam blok mono, tiga aksi (Approve/Edit/Reject) jelas di kanan bawah. Ini momen kritis produk — harus menonjol tapi tidak alarming.
- **Thread scroll:** auto-scroll ke bawah saat streaming, tapi berhenti auto-scroll jika user scroll ke atas (pola ChatGPT).

### 6.3 Card & Panel

- bg `bg-elevated`, border 1px `border`, radius-lg (12px), padding 20–24px.
- Header card: title 15–16px weight 600 + aksi kanan; dipisah divider halus bila perlu.
- Hover interaktif: border berubah ke `border-strong` + shadow-sm — bukan scale/lift.

### 6.4 Form & Input

- Label selalu terlihat di atas field (12.5px, weight 500, fg-muted) — **tidak pernah** placeholder sebagai satu-satunya label.
- Input: bg elevated (light) / bg-muted (dark), border, radius-md, tinggi 34px, focus ring accent.
- Error: teks destructive 12px di bawah field + border destructive; bukan hanya warna.
- Group form dalam card dengan heading section.

### 6.5 Table & List

- Header: caption 12px uppercase tracking-wide fg-subtle, tanpa background.
- Row: border-bottom 1px border, hover bg-muted, tinggi min 44px.
- Aksi row di kanan sebagai ghost button ikon.

### 6.6 Badge & Status

- Badge: radius-sm, 11.5px weight 500, padding 2px 8px, varian: neutral (bg-muted), accent (bg accent-muted + teks accent), success, warning, destructive.
- Status dot 8px radius-full + pulse halus saat "checking".

### 6.7 Terminal & Code

- bg `oklch(16% 0.005 80)` (selalu gelap di kedua mode), teks mono 13px, header bar dengan 3 dot decoratif + judul.
- Code inline: bg-muted, radius-sm, padding 1px 6px.

### 6.8 Graph Canvas

- Wrapper card gelap konsisten dengan terminal; node agent memakai token warna sama (accent untuk node terpilih); hindari slate dingin.

### 6.9 Empty State

- Ikon Lucide 40px fg-subtle, judul Serif, deskripsi fg-muted, satu CTA. Ini pola Claude yang hangat — bukan error, tapi undangan.

### 6.10 Modal

- Overlay `rgba(20,18,15,0.5)` + backdrop-blur 4px; panel elevated radius-lg shadow-lg max-width 480px; animasi fade+rise 200ms.

---

## 7. Motion

| Interaksi | Durasi | Easing | Catatan |
|---|---|---|---|
| Hover state | 120–150ms | ease-out | Warna & border saja |
| Perubahan tab/view | 180ms | ease-out | Fade-in konten |
| Modal in/out | 200ms / 150ms | ease-out / ease-in | Exit lebih cepat dari enter |
| Pesan baru masuk | 200ms | ease-out | Fade + translateY 8px |
| Streaming caret | 1s loop | step-end | Blink |
| Skeleton / 3-dot | 1.2s loop | ease-in-out | Pulse |

- **Hanya** `opacity` dan `transform` yang dianimasikan — tidak pernah `width/height/top/left`.
- `prefers-reduced-motion: reduce` → semua transisi ≤ 1ms, tanpa loop animasi.
- GSAP/Framer Motion tidak dipakai di v1 (UI app, bukan landing page); tersedia sebagai opsi untuk halaman marketing di masa depan.

---

## 8. Aksesibilitas

- [ ] Kontras teks ≥ 4.5:1 (semua token di atas sudah diverifikasi untuk light & dark).
- [ ] Focus ring 2px selalu terlihat; **tidak pernah** `outline: none` tanpa pengganti.
- [ ] Semua tombol ikon-only punya `aria-label`.
- [ ] Navigasi keyboard penuh: sidebar item adalah `<button>`/`<a>` asli, bukan `div` klik.
- [ ] Touch target ≥ 44×44px pada mobile.
- [ ] `prefers-color-scheme` dihormati sebagai default; toggle manual disimpan di localStorage.
- [ ] `prefers-reduced-motion` dihormati.
- [ ] Layar 375px: tanpa horizontal scroll.

---

## 9. Ikon

- Library: **Lucide React** (sudah terpasang). Tidak ada emoji sebagai ikon.
- Ukuran: 18px (nav/aksi), 16px (inline), 40px (empty state). Stroke 1.75px.
- Pemetaan nav: Chat → `MessageSquare`, Agents → `Bot`, Tools → `Wrench`, Knowledge → `Library`, Models → `Cpu`, Credentials → `KeyRound`, Dashboard → `LayoutDashboard`, theme → `Sun`/`Moon`.

---

## 10. Struktur File & Implementasi

```
frontend/src/
├── index.css        # Tailwind v4 @theme tokens, dark variant, base, font imports
├── App.css          # Semua class komponen (memakai var(--color-*))
├── App.tsx          # App shell: sidebar + topbar + theme toggle + routing tab
└── components/
    ├── ConversationsView.tsx  # Chat UI (thread + composer + HITL card)
    └── ...            # View lain: restyle otomatis via App.css
```

**Aturan kontribusi:**

1. Warna/spacing/radius hanya via token CSS — tidak ada hex/px ajaib di komponen.
2. Komponen baru wajib pakai class semantic yang sudah ada, atau tambah di App.css dengan token.
3. Setiap state async wajib punya feedback (skeleton/pulse/disabled+label).
4. Icon hanya dari Lucide; SVG custom harus `aria-hidden` atau berlabel.

---

## 11. Checklist Pra-Rilis UI

- [ ] Tidak ada emoji sebagai ikon; semua ikon Lucide.
- [ ] `cursor: pointer` untuk semua elemen klik.
- [ ] Hover transition 120–300ms di semua interaktif.
- [ ] Kontras light & dark ≥ 4.5:1 (uji dengan devtools).
- [ ] Focus state terlihat untuk keyboard nav.
- [ ] `prefers-reduced-motion` dihormati.
- [ ] Responsive di 375px / 768px / 1024px / 1440px.
- [ ] Streaming menampilkan token progresif + caret; tidak ada spinner blokir.
- [ ] Interrupt HITL punya aksi jelas (Approve/Edit/Reject) dengan keyboard-accessible.
- [ ] Dark mode: uji tanpa flash putih saat load (class `.dark` set sebelum render).
