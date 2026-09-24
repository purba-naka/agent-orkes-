# PRD: Platform agent orchestrator berbasis LangChain dan LangGraph

**Status:** Draft disetujui untuk implementasi  
**Tanggal:** 16 September 2026  
**Versi:** 1.0  
**Pemilik:** Project owner

## Ringkasan

Produk ini adalah platform untuk membuat, menyusun, menjalankan, dan memantau
AI agent tanpa menulis Python. Produk mengambil konsep utama PINTER, yaitu agent
dikonfigurasi sebagai data, tetapi menggunakan LangChain sebagai agent harness
dan LangGraph sebagai mesin orkestrasi serta durable execution.

Versi pertama berfokus pada satu pengguna yang dapat membuat agent melalui UI,
memberinya tool, menyusun beberapa agent menjadi graf bercabang, menjalankan
graf sambil melihat streaming native, dan melanjutkan eksekusi setelah human in
the loop (HITL) pada proses server yang berbeda.

Prinsip utama produk adalah:

> Agent adalah data, bukan kode.

Runtime generik membaca revisi konfigurasi immutable dari database, membangun
harness LangChain dan graf LangGraph, lalu menjalankannya dengan persistence
native. User tidak menulis Python, ekspresi, template logic, atau predikat kode.

## Latar belakang

PINTER membuktikan nilai produk berupa agent studio, katalog model dan tool,
percakapan, orkestrasi multi-agent, streaming, dan HITL. Namun, implementasi
PINTER yang aktif menggunakan struktur orkestrasi berbentuk pohon. Modul kanvas
workflow lama dengan node `start`, `agent`, `task`, `condition`, dan `end` tidak
menjadi jalur runtime aktif dan tidak menjadi acuan produk ini.

Produk ini mempertahankan konsep yang berguna, tetapi mengganti fondasi runtime:

- LangChain `create_agent()` membangun agent harness.
- LangGraph `StateGraph` menyusun topologi multi-agent.
- LangGraph checkpointer menyediakan durable execution.
- LiteLLM menyediakan akses multi-provider, routing, dan fallback model.
- Middleware native LangChain menyediakan kontrol konteks, retry, guardrail,
  dan HITL.

## Tujuan

Versi pertama harus mencapai tujuan berikut:

1. Memungkinkan user membuat agent sepenuhnya melalui UI.
2. Menyimpan agent sebagai konfigurasi data dan revisi immutable.
3. Mendukung agent tunggal dan graf multi-agent dengan satu compiler runtime.
4. Mendukung percabangan, fan-out, fan-in, dan loop terkendali.
5. Mendukung tool katalog, tool kode terdaftar, sub-agent, dan agent-as-tool.
6. Menjalankan graf dengan streaming native LangGraph.
7. Mempertahankan state melalui PostgreSQL checkpointer.
8. Menghentikan dan melanjutkan run melalui HITL lintas proses.
9. Mengulang node gagal tanpa menjalankan ulang node hulu yang sudah sukses.
10. Menolak konfigurasi yang tidak dapat dieksekusi sebelum dipublish.
11. Menggunakan prompt dan context engineering yang efisien serta dapat diuji.
12. Menyediakan fondasi yang dapat dikembangkan untuk eksperimen LangChain dan
    LangGraph berikutnya.

## Definisi keberhasilan versi pertama

Versi pertama selesai ketika:

> Seorang user membuat agent lewat UI, memberinya tool, menyusunnya menjadi graf
> bercabang bersama agent lain, menjalankannya dan melihat hasilnya mengalir;
> graf itu berhenti untuk meminta persetujuan tool, lalu user menyetujuinya pada
> hari berikutnya di proses server yang berbeda, dan eksekusi berlanjut dari
> titik tersebut.

## Bukan tujuan

Fitur berikut tidak termasuk dalam produk:

- Multi-tenant.
- Role-based access control (RBAC).
- Kuota pengguna atau organisasi.
- Sharing agent.
- Template marketplace.
- Wiki.

Fitur berikut ditunda setelah versi pertama:

- Memori berjenjang berupa profile, episodic, dan semantic memory.
- Outbox dan audit trail lengkap.
- Evaluation framework dan prompt evaluation dashboard.
- Time-travel UI.
- Knowledge ingestion dan indexing pipeline.
- State schema per graf yang didefinisikan user.
- Background worker dan reconnectable event stream.
- Graceful shutdown melalui `RunControl` dan `request_drain()`.
- `error_handler` per node dengan `Command(goto=...)`.
- Fan-in `any` dan quorum.
- Migrasi checkpoint antar-revisi graf.
- Reproduksi implementasi `@tool` lintas deployment.

## Persona dan kebutuhan utama

### Agent builder

Agent builder ingin membuat agent dan workflow dari UI tanpa menulis kode. Ia
membutuhkan prompt editor, pemilihan model, pemilihan tool, kanvas graf,
validasi, publish revision, dan test run.

### Agent operator

Agent operator ingin menjalankan percakapan atau pekerjaan orkestrasi, melihat
streaming hasil, meninjau permintaan HITL, melakukan retry, dan membaca status
run.

Pada versi pertama, kedua persona dapat merupakan pengguna yang sama.

## Prinsip desain produk

### Semua adalah graf

Agent chat sederhana dan orkestrasi kompleks menggunakan model runtime yang
sama. Agent sederhana merupakan graf sepele yang dihasilkan langsung oleh
`create_agent()`. Agent kompleks merupakan `StateGraph` yang menyusun beberapa
agent, tool, conditional edge, dan subgraph.

Produk tidak menyediakan dua mesin runtime terpisah.

### Topologi adalah prosedur

Langkah kerja disimpan sebagai node dan edge. Prompt tidak menyimpan prosedur
panjang yang seharusnya digambar sebagai graf.

Aturan produk adalah:

> Jika tergoda menulis methodology, gambarlah edge.

### Gunakan fitur native terlebih dahulu

Produk tidak membuat ulang fitur yang sudah disediakan LangChain, LangGraph,
atau LiteLLM. Streaming, persistence, interrupt, retry, middleware, structured
output, routing model, dan fallback memakai mekanisme native sejauh tersedia.

### Validasi sebelum runtime

Runtime hanya menjalankan revisi published yang telah lolos validasi. Kesalahan
referensi, topologi, schema mapping, dan kebijakan sub-agent harus ditemukan
sebelum run dimulai.

## Model domain

### Agent

`agent` adalah identitas stabil yang memiliki nama, metadata, draft aktif, dan
revisi published aktif. Agent tidak menyimpan perilaku mutable yang dipakai run
secara langsung.

### Agent revision

`agent_revision` adalah snapshot immutable yang menyimpan:

- `system_prompt`.
- `input_schema`.
- `output_schema`.
- `context_policy`.
- `middleware_policy`.
- Node dan edge.
- Named exits.
- `recursion_limit`.
- Referensi immutable ke model revision.
- Referensi immutable ke tool revision.
- Referensi immutable ke sub-agent revision.

Publish membuat revisi baru. Run selalu mengikat satu `agent_revision_id`.
Revisi yang masih dirujuk run tidak dapat dihapus.

### Model dan model revision

`model` adalah identitas stabil. `model_revision` adalah konfigurasi immutable
LiteLLM yang memuat:

- Nama tampilan.
- Provider.
- Model ID.
- Base URL opsional.
- `api_key_id` opsional.
- Default parameters.
- Status aktif.
- Konfigurasi virtual model untuk fallback atau round-robin.
- Daftar underlying model revisions.

Secret tidak disalin ke revisi. Secret tetap direferensikan melalui
`api_key_id`. Jika secret dinonaktifkan, run lama gagal dengan error eksplisit.

Pricing dan metadata kapabilitas model ditunda. Jika dibutuhkan, runtime dapat
memakai metadata LiteLLM seperti dukungan function calling.

### Tool dan tool revision

`tool` adalah identitas stabil. `tool_revision` menyimpan konfigurasi immutable,
input schema, output schema, dan referensi secret. Versi pertama mendukung tiga
tipe tool data:

- `http`.
- `mcp`.
- `retrieval`.

Runtime juga memiliki registry kode untuk fungsi berdekorasi `@tool`. Tool kode
tidak disimpan sebagai source code di database. Ia menunjuk
`implementation_key` dan `implementation_version` yang tersedia pada deployment.

### Conversation

`conversation` adalah thread jangka panjang untuk mode percakapan. Conversation
menyimpan `agent_revision_id` dan menggunakan `conversation_id` sebagai
LangGraph `thread_id`.

Conversation terkunci ke revisi awal. Upgrade agent bersifat eksplisit dan
membuat thread baru dengan ringkasan conversation lama. Checkpoint tidak
dimigrasikan antar-topologi.

### Run

`run` adalah proyeksi status eksekusi. Ia menyimpan:

- `run_id`.
- `agent_revision_id`.
- Mode eksekusi.
- Status.
- Named result.
- Usage.
- Waktu mulai dan selesai.
- Error yang telah disanitasi.

Untuk mode orkestrasi, `run_id` juga menjadi LangGraph `thread_id`. Untuk mode
percakapan, checkpoint menggunakan `conversation_id`, sedangkan setiap giliran
masih memiliki `run_id` untuk streaming, HITL, dan observability.

### Message

`message` adalah proyeksi baca untuk UI conversation. Checkpointer tetap menjadi
sumber kebenaran. Tabel messages tidak pernah menjadi input runtime dan dapat
dibangun ulang dari checkpoint.

## Arsitektur runtime

### Compiler

Compiler menerima satu `agent_revision` beserta seluruh dependency revision,
kemudian menghasilkan compiled graph.

Jika revisi hanya memiliki satu node agent, compiler dapat mengembalikan hasil
`create_agent()` secara langsung. Jika revisi memiliki topologi lebih besar,
compiler membangun `StateGraph` dan memasukkan hasil `create_agent()` sebagai
node atau subgraph.

Compiler harus:

1. Memuat seluruh dependency revision yang sudah dikunci.
2. Membuat model melalui `ChatLiteLLM` atau `ChatLiteLLMRouter`.
3. Menyelesaikan tool data dan tool registry.
4. Menyusun middleware dalam urutan yang ditentukan runtime.
5. Membuat setiap agent node melalui `create_agent()`.
6. Membuat tool node deterministik.
7. Memasang node retry policy, timeout policy, dan graph-wide error handler.
8. Menyusun edge, conditional edge, fan-out, fan-in, dan named exits.
9. Mengompilasi root dengan PostgreSQL checkpointer.
10. Menyertakan Store hanya ketika long-term memory ditambahkan kelak.

### State universal versi pertama

Semua graf menggunakan satu `TypedDict` state universal:

- `messages`: memakai reducer `add_messages`.
- `outputs`: dictionary dengan key `node_id`, memakai reducer merge.
- `result_name`: named exit yang dipilih.
- `run_id`: ID run saat ini.
- Field kontrol internal yang diperlukan runtime.

Setiap node hanya menulis ke `outputs[node_id]`. Ini mencegah benturan state saat
cabang paralel selesai pada superstep yang sama.

State per-graf yang dikonfigurasi user ditunda.

## Jenis node

Versi pertama hanya memiliki dua jenis node yang disimpan user.

### Agent node

Agent node menjalankan `create_agent()` dengan model, prompt, tools, structured
output, dan middleware yang telah dikompilasi. Agent node dapat berupa:

- Inline agent yang didefinisikan dalam root revision.
- Node `ref` yang menunjuk sub-agent revision immutable.

### Tool node

Tool node memanggil tepat satu tool secara deterministik tanpa LLM. Input tool
dipetakan dari output node hulu. Tool node dipakai saat topologi, bukan model,
harus menentukan bahwa tool selalu dijalankan.

Produk tidak menyediakan node `transform`. Pembentukan data dilakukan oleh
structured output agent hulu atau mapping deklaratif tool.

Routing bukan node. Routing hidup pada conditional edge.

## Root agent dan sub-agent

Setiap agent published dapat menjadi root graph. Root dapat berupa satu
`create_agent()` atau `StateGraph` multi-node.

Sub-agent didukung melalui dua mekanisme yang berbeda.

### Sub-agent sebagai node `ref`

Node `ref` dipilih oleh topologi. Ia selalu terlihat pada kanvas, boleh memiliki
HITL, dan dapat diinspeksi sebagai subgraph statis.

Sub-agent `ref` memakai state privat. Parent memetakan input ke sub-agent dan
hanya menerima named result yang tervalidasi. Internal `messages` dan `outputs`
sub-agent tidak digabung ke parent. Streaming internal tetap tersedia melalui
`subgraphs=True`.

### Agent sebagai tool

Agent-as-tool dipilih oleh LLM pemanggil dan dapat dipanggil nol, satu, atau
beberapa kali. Ia tidak terlihat sebagai node pada kanvas.

Karena sub-agent di dalam tool tidak ditemukan secara statis oleh LangGraph,
validator melarang agent-as-tool jika agent tersebut atau seluruh turunannya
mengandung HITL. Jika HITL dibutuhkan, user harus memakai node `ref`.

Pesan validasi UI adalah:

> Agent ini memuat langkah persetujuan manusia, jadi tidak bisa dipakai sebagai
> tool. Tambahkan sebagai node agar persetujuannya terlihat di kanvas.

Agent-as-tool memakai checkpointer yang diwariskan dengan state segar per
invocation dan mewarisi kebijakan retry `@task` dari node pemanggil.

## Topologi graf

### Entry dan exit

Graf memiliki tepat satu entry dari LangGraph `START`. Produk tidak membuat node
`start` atau `end` sendiri.

Satu atau beberapa node dapat menuju LangGraph `END`. Setiap edge ke `END`
memiliki `result_name`, seperti `success`, `rejected`, atau `not_found`.

Hasil final berbentuk:

```json
{
  "result_name": "success",
  "output": {},
  "run_id": "uuid",
  "usage": {}
}
```

Output node terminal harus cocok dengan `output_schema` root graph.

### Sequential, parallel, dan loop

Sequential, parallel, dan loop merupakan preset UI yang menghasilkan edge,
bukan jenis runtime atau jenis agent.

Graf boleh memiliki siklus edge. Graf yang memiliki siklus wajib memiliki
conditional exit dan `recursion_limit`. Default `recursion_limit` adalah 25.

Siklus dependency melalui node `ref` dilarang. Misalnya, A mereferensikan B dan
B mereferensikan A harus ditolak saat publish karena compiler tidak dapat
menyelesaikan dependency tree.

### Fan-out dan fan-in

Fan-out memakai beberapa edge keluar dari satu node. Fan-in versi pertama hanya
mendukung `join: all` dan dikompilasi sebagai:

```python
builder.add_edge(["research", "analysis"], "writer")
```

Node hilir berjalan setelah semua sumber berhasil. Fan-in `any` dan quorum
ditunda karena membutuhkan pembatalan cabang dan menghasilkan perilaku yang
bergantung timing.

## Conditional edge

Produk mendukung dua bentuk routing.

### Routing semantik

Agent hulu mengeluarkan field enum melalui `output_schema`. Edge memetakan nilai
enum ke node tujuan. Tidak ada model router kedua.

Contoh konfigurasi:

```json
{
  "source": ["triage", "route"],
  "routes": {
    "billing": "billing_agent",
    "technical": "support_agent"
  }
}
```

### Routing mekanis

Conditional edge mekanis membandingkan satu field dengan satu nilai. Ia tidak
mendukung `AND`, `OR`, nesting, scripting, atau ekspresi bebas.

Contoh konfigurasi:

```json
{
  "source": ["http_request", "status"],
  "operator": "eq",
  "value": "success",
  "then": "writer",
  "else": "failure_handler"
}
```

Jika alur membutuhkan beberapa syarat, user harus menggambar beberapa edge agar
logikanya terlihat pada kanvas.

## Mapping input tool

Tool node menggunakan mapping field-to-field murni:

```json
{
  "to": ["compose_email", "recipient"],
  "subject": ["compose_email", "subject"],
  "body": ["compose_email", "body"]
}
```

Mapping tidak mendukung interpolasi string, expression language, fallback, atau
transform. Validator mencocokkan field sumber dan tipe terhadap input schema
tool saat publish. Jika data perlu digabung atau dibentuk ulang, agent hulu
harus mengeluarkan bentuk tersebut melalui `output_schema`.

## Agent harness

LangChain mendefinisikan harness sebagai model, prompt, tools, dan middleware.
Produk menggunakan `create_agent()` sebagai harness dan tidak membuat framework
harness sendiri.

### Middleware baseline wajib

Compiler memasang middleware berikut pada agent node:

- `ModelCallLimitMiddleware`.
- `ToolCallLimitMiddleware`.
- `ModelRetryMiddleware`.
- `ToolErrorMiddleware`.
- Middleware prompt dinamis untuk context assembly.

`ToolErrorMiddleware` hanya mengekspos error yang telah disanitasi. Raw exception
message tidak boleh dikirim ke model secara otomatis.

### Middleware opsional

`middleware_policy` pada agent revision dapat mengaktifkan middleware berikut:

- `SummarizationMiddleware`.
- `ContextEditingMiddleware`.
- `HumanInTheLoopMiddleware`.
- `LLMToolSelectorMiddleware` saat jumlah tool besar.
- `PIIMiddleware` bila diperlukan.

User memilih kapabilitas dan parameter yang diizinkan, tetapi compiler
menentukan urutan middleware.

### Middleware yang tidak dipakai pada versi pertama

Versi pertama tidak memakai:

- `FilesystemMiddleware`.
- `ShellToolMiddleware`.
- `TodoListMiddleware`.
- `SubAgentMiddleware`.
- `ModelFallbackMiddleware`.
- `ToolRetryMiddleware`.

`SubAgentMiddleware` tidak diperlukan karena produk memiliki node `ref` dan
agent-as-tool. Fallback model ditangani `ChatLiteLLMRouter`. Retry tool ditangani
oleh `@task`; menambahkan `ToolRetryMiddleware` dapat mengalikan jumlah attempt.

## Prompt dan context engineering

### Model prompt

Agent menyimpan tiga field prompt-related:

- `system_prompt TEXT`.
- `output_schema JSONB`.
- `context_policy JSONB`.

UI membantu user menyusun satu `system_prompt` dalam empat blok:

1. `## Role`: siapa agent dan hasil yang harus diberikan, satu sampai tiga
   kalimat.
2. `## Rules`: aturan keputusan dan penggunaan tool. Setiap larangan harus
   memiliki perilaku cadangan yang positif.
3. `## Output`: dibuat otomatis dari `output_schema`.
4. `## Examples`: nol sampai tiga contoh, hanya untuk bentuk yang sulit
   dijelaskan.

`identity` dan `mission` digabung menjadi Role. `boundaries` menjadi aturan
positif dengan fallback. `methodology` dihapus karena prosedur harus hidup pada
edge.

### Context assembly

Compiler merakit prompt pada setiap model call dalam tiga lapis berdasarkan
volatilitas:

1. Lapisan stabil berada paling awal: `system_prompt` dan deskripsi tool. Posisi
   ini mendukung prefix caching.
2. Lapisan lambat berada di tengah: memori Store dan preferensi user ketika
   fitur tersebut ditambahkan.
3. Lapisan volatil berada paling akhir sebelum `messages`: retrieved knowledge
   dan `state["outputs"]` dari node hulu.

Lapisan stabil diberikan melalui `system_prompt` statis pada `create_agent()`.
Lapisan lambat dan volatil disisipkan oleh middleware yang menulis ulang prompt
per model call. Compiler tidak merakit satu string permanen pada waktu startup.

`context_policy` mengatur secara eksplisit konteks yang boleh dimasukkan, dengan
struktur awal:

```json
{
  "memory": false,
  "knowledge_top_k": 0,
  "upstream": ["research", "analysis"]
}
```

### Context percakapan panjang

Checkpoint menyimpan seluruh message history. `SummarizationMiddleware` hanya
mengatur konteks yang dikirim ke model.

Default policy:

- Trigger summarization saat context mencapai 70% dari context window model.
- Pertahankan 30% context terbaru.
- Simpan summary dalam state/checkpoint.
- Jangan memotong tool call dan tool result yang masih berpasangan.
- Gunakan `ContextEditingMiddleware` untuk membersihkan tool output lama jika
  tool results mendominasi context.

Jika LiteLLM model profile tidak menyediakan context window, model revision
harus menyimpan nilai manual atau policy harus menggunakan batas token absolut.

## Integrasi LiteLLM

Produk menggunakan integrasi LangChain `langchain-litellm` secara in-process,
seperti penggunaan LiteLLM pada PINTER.

Model tunggal menggunakan `ChatLiteLLM`. Virtual model menggunakan
`ChatLiteLLMRouter` dan `litellm.Router` untuk fallback atau round-robin.

Resolver selalu mengembalikan objek chat model, termasuk saat kredensial
diambil dari environment. Resolver tidak boleh memiliki jalur yang kadang
mengembalikan string model dan kadang objek model.

Integrasi harus mempertahankan:

- Tool calling.
- Structured output.
- Async invocation.
- Token-level streaming.
- Token usage metadata.
- Provider-specific model prefixes.
- Custom base URL.

Objek `ChatLiteLLM` tidak disimpan atau diserialisasi. Compiler membuatnya dari
`model_revision` saat diperlukan.

## Persistence dan durable execution

### Sumber kebenaran

LangGraph checkpointer adalah satu-satunya sumber kebenaran state eksekusi.
Produksi menggunakan `PostgresSaver`. `InMemorySaver` hanya boleh dipakai untuk
test dan development sementara.

Setiap invocation wajib memiliki `thread_id`:

- Mode conversation: `thread_id = conversation_id`.
- Mode orchestration: `thread_id = run_id`.

### Proyeksi baca

Tabel `messages` dan `runs` merupakan proyeksi searah dari checkpoint dan stream
updates. Runtime tidak pernah membaca proyeksi untuk melanjutkan execution.
Jika proyeksi hilang, sistem dapat membangunnya ulang dari checkpoint.

### Revision pinning

Run menyimpan `agent_revision_id`. Agent revision mengunci semua model revision,
tool revision, node `ref`, dan agent-as-tool revision secara transitif.

Resume dan retry selalu mengompilasi revision tree yang sama. Edit atau publish
agent baru tidak mengubah run yang sedang berjalan atau tertunda.

## Retry, timeout, dan kegagalan

### Node retry

Compiler memakai native LangGraph `RetryPolicy` pada node. Default policy
menggunakan exponential backoff dan jitter untuk error transient.

Retry LangGraph terjadi pada node yang gagal, bukan dari awal graf. Checkpoint
dibuat pada batas superstep. Node hulu yang sudah sukses tidak dijalankan ulang,
dan write dari attempt gagal dibuang.

Node diulang dari awal fungsi node, bukan dari baris yang gagal.

### Tool task checkpointing

Setiap panggilan tool di dalam node dibungkus satu wrapper generik berbasis
LangGraph `@task`. Wrapper ini dipakai semua tool dan tidak menghasilkan kode
khusus per tool.

Aturannya:

- Urutan pemanggilan task dalam node harus deterministik.
- Jangan mengiterasi collection yang tidak berurutan untuk memanggil task.
- Jangan memakai waktu atau random secara langsung untuk menentukan urutan.
- Semua operasi nondeterministik harus masuk ke task.
- Output task harus JSON-serializable.
- Tool mutatif harus menerima idempotency key atau memakai `max_attempts=1`.
- Task yang mulai tetapi belum menyelesaikan checkpoint dapat dijalankan ulang.

Saat node diulang, task yang telah sukses dimuat dari checkpoint dan tidak
dipanggil ulang.

### Timeout

Node async dapat memakai native `TimeoutPolicy` dengan `run_timeout` dan
`idle_timeout`. Node sync yang membutuhkan timeout harus diubah menjadi async.

### Error handler

Graf memakai `set_node_defaults(error_handler=...)` untuk mencatat status
`failed` dan error tersanitasi ke proyeksi run. Error handler per node yang
melompat melalui `Command(goto=...)` ditunda karena menciptakan edge yang tidak
terlihat di kanvas.

Jika user membutuhkan failure path, node harus menghasilkan output error
terstruktur dan conditional edge biasa harus menentukan tujuan berikutnya.

### Resume lintas proses

Setelah proses mati atau request dibatalkan, runtime melanjutkan checkpoint
dengan input `None` dan config/thread yang sama. Eksekusi dapat dilanjutkan pada
proses atau server yang berbeda selama database dan revision dependency masih
tersedia.

## Human in the loop

Produk menyediakan dua titik HITL melalui satu mekanisme LangGraph interrupt.

### Tool approval

`HumanInTheLoopMiddleware` menghentikan tool berisiko sebelum eksekusi. User
dapat memilih:

- `approve`.
- `reject`.
- `edit`.

### Node output review

Node dapat memanggil `interrupt()` setelah menghasilkan draft. User dapat
memilih:

- `accept`.
- `revise` dengan teks pengganti.
- `abort`.

Keduanya dilanjutkan melalui `Command(resume=...)` pada endpoint resume yang
sama.

Interrupt tidak memiliki timeout runtime. Jeda dapat bertahan tanpa batas.
Kebijakan expiry, jika kelak diperlukan, menjadi kebijakan produk melalui job
terjadwal, bukan polling atau timeout pada proses eksekusi.

`interrupt()` melempar `GraphBubbleUp`, sehingga tidak ditangkap retry policy
atau error handler sebagai kegagalan biasa.

## Streaming

Produk memakai LangGraph streaming native:

```python
graph.astream(
    input,
    config,
    stream_mode=["messages", "updates", "custom"],
    subgraphs=True,
)
```

`subgraphs=True` wajib sejak versi pertama agar format protokol tidak perlu
diubah saat UI mulai menampilkan sub-agent. Client menerima namespace subgraph,
stream mode, dan chunk native.

Produk hanya menulis dua lapisan transport:

1. Encoder yang mengubah objek Python seperti `AIMessageChunk` dan `Interrupt`
   menjadi JSON-safe payload.
2. Bingkai pembuka dan penutup yang memuat `run_id`, status akhir, named result,
   dan total usage.

Produk tidak membuat taksonomi event paralel seperti `node.started` atau
`token.received` jika data yang sama tersedia pada stream native. Metadata
`langgraph_node`, namespace subgraph, tool calls, dan `__interrupt__` tetap
dipertahankan.

## Perilaku saat koneksi terputus

Versi pertama bersifat request-bound. Jika koneksi streaming terputus, server
membatalkan invocation dan mempertahankan checkpoint terakhir. Status proyeksi
menjadi `cancelled`.

User dapat memanggil endpoint retry untuk melanjutkan dengan input `None` dan
checkpoint yang sama. Node yang telah selesai tidak diulang. Background worker
dan reconnectable event stream ditunda.

## API produk

### Conversation API

`POST /conversations/{conversation_id}/messages` menambah satu giliran ke
conversation. Endpoint menggunakan `thread_id = conversation_id`, membuat
`run_id` untuk giliran tersebut, dan mengembalikan stream native.

### Orchestration API

`POST /agents/{agent_id}/runs` memulai run satu kali pada agent revision aktif.
Endpoint membuat `run_id`, menggunakan `thread_id = run_id`, dan mengembalikan
stream native.

### Resume API

`POST /runs/{run_id}/resume` melanjutkan HITL dengan keputusan yang tervalidasi.
Client wajib menyimpan `run_id` dari bingkai pembuka stream.

### Retry API

`POST /runs/{run_id}/retry` melanjutkan run yang gagal, dibatalkan, atau
terputus. Endpoint tidak menerima keputusan HITL dan memanggil graf dengan input
`None`.

### Read API minimum

Versi pertama memerlukan read API untuk:

- Detail agent dan draft.
- Daftar dan detail agent revisions.
- Daftar model dan model revisions.
- Daftar tool dan tool revisions.
- Conversation history dari proyeksi messages.
- Run status dan hasil dari proyeksi runs.
- Pending interrupts untuk satu run.

Kontrak CRUD detail menjadi bagian technical design, bukan PRD ini.

## Validasi

### Validasi autosave draft

Autosave memungkinkan graf sementara yang belum executable, tetapi harus
menolak:

- JSON atau schema konfigurasi malformed.
- ID node duplikat.
- Tipe node yang tidak dikenal.
- Field yang telah diisi dengan tipe salah.
- Secret mentah dalam konfigurasi yang seharusnya memakai `api_key_id`.

### Validasi publish

Publish harus menolak:

- Siklus dependency antar-agent melalui node `ref`.
- Node yang tidak terjangkau dari `START`.
- Node yang tidak memiliki jalur menuju `END`.
- Referensi agent, model, tool, revision, atau secret yang rusak/nonaktif.
- Input/output mapping yang menunjuk field tidak ada.
- Ketidakcocokan tipe antara output hulu dan input tool/sub-agent.
- Agent-as-tool yang memiliki HITL pada dirinya atau turunannya.
- Loop tanpa conditional exit.
- `recursion_limit` tidak valid.
- Output tool kode yang tidak JSON-serializable.
- Tool registry implementation yang tidak tersedia.
- Model atau API key yang tidak tersedia.
- Named exit yang tidak cocok dengan root output schema.
- Fan-in selain `join: all`.
- Routing mekanis dengan lebih dari satu perbandingan atau expression bebas.
- Dependency yang tidak dapat dikunci ke immutable revision.

Runtime hanya menerima revisi published.

## Keamanan

Versi pertama tetap wajib menerapkan batas keamanan berikut meski multi-tenant
dan RBAC tidak termasuk scope:

- Enkripsi credential pada penyimpanan.
- Secret hanya diakses oleh model/tool resolver saat runtime.
- Secret tidak masuk prompt, checkpoint, stream, log, atau proyeksi.
- Input HTTP dan MCP divalidasi terhadap schema.
- HTTP tool menerapkan allowlist/denylist jaringan untuk mencegah SSRF.
- Error eksternal disanitasi sebelum masuk model atau stream.
- Tool mutatif mendukung idempotency key.
- HITL wajib untuk tool yang ditandai berisiko oleh konfigurasi.
- PII middleware dapat diaktifkan untuk input, output, dan tool results.
- Prompt dan retrieved content diperlakukan sebagai data tidak tepercaya.

Detail threat model menjadi technical design terpisah.

## Observability dan usage

Versi pertama mencatat metadata minimum berikut dari stream dan model response:

- `run_id` dan `thread_id`.
- `agent_revision_id`.
- Node dan subgraph namespace.
- Status run.
- Waktu mulai dan selesai.
- Model revision yang dipakai.
- Input, output, dan total token jika provider menyediakan metadata.
- Jumlah attempt node/task.
- Interrupt status.
- Error class yang telah disanitasi.

Produk tidak membuat sistem tracing baru pada versi pertama. Integrasi LangSmith
bersifat opsional untuk development dan debugging.

## Pengalaman UI minimum

### Agent editor

UI agent editor harus menyediakan:

- Metadata agent.
- Prompt editor empat blok.
- JSON Schema editor atau form builder untuk input/output.
- Pemilih model revision.
- Pemilih tool dan agent-as-tool.
- Middleware capability toggles yang diizinkan.
- Kanvas node dan edge.
- Configurator routing semantik dan mekanis.
- Configurator mapping field-to-field.
- Validation panel untuk draft dan publish.
- Publish action yang menghasilkan revision immutable.

### Run viewer

UI run viewer harus menyediakan:

- Token streaming.
- Node/subgraph yang sedang aktif dari metadata native.
- Tool call dan hasil yang aman ditampilkan.
- Pending HITL dengan konteks yang tersedia.
- Tombol approve, edit, reject, accept, revise, dan abort sesuai interrupt.
- Status cancelled, failed, completed, dan interrupted.
- Tombol retry untuk run yang dapat dilanjutkan.
- Named result dan usage final.

### Conversation UI

Conversation UI harus menyediakan message history, streaming response, pending
HITL, revision yang terkunci, dan tindakan **Upgrade agent** yang membuat thread
baru dengan ringkasan thread lama.

## Kriteria penerimaan

Versi pertama diterima jika seluruh skenario berikut lulus.

### Agent sederhana

1. User membuat agent satu node melalui UI.
2. User memilih LiteLLM model dan menambahkan HTTP tool.
3. User mempublish revisi.
4. Conversation menghasilkan token stream.
5. Giliran kedua dengan conversation yang sama mengingat giliran pertama.

### Graf bercabang

1. User membuat root graph dengan dua sub-agent `ref` paralel.
2. Kedua cabang menghasilkan structured output.
3. Fan-in menunggu kedua cabang.
4. Conditional edge memilih named exit berdasarkan enum output.
5. Stream menampilkan namespace setiap subgraph.

### Durable HITL

1. Tool berisiko memicu `HumanInTheLoopMiddleware`.
2. Run berhenti dan proses server dapat dimatikan.
3. Pada proses server baru, user memanggil resume dengan `run_id` yang sama.
4. Tool berjalan hanya setelah approval.
5. Node hulu tidak dijalankan ulang.

### Retry dari node gagal

1. Node tool gagal karena error transient.
2. Retry policy mengulang node yang gagal.
3. Node hulu yang sudah sukses tidak dijalankan ulang.
4. Task yang sudah selesai dimuat dari checkpoint.
5. Run dapat diteruskan melalui endpoint retry setelah disconnect.

### Versioning

1. Run tertunda mengikat agent revision lama.
2. User mempublish revision baru.
3. Resume run lama tetap menggunakan revision tree lama.
4. Run baru memakai revision baru.

### Validator

1. Draft yang belum memiliki edge dapat disimpan.
2. Draft yang sama tidak dapat dipublish.
3. Siklus edge dengan conditional exit dapat dipublish.
4. Siklus agent `ref` ditolak.
5. Agent-as-tool dengan HITL ditolak.
6. Mapping schema yang tidak cocok ditolak.

## Metrik keberhasilan

Versi pertama menggunakan metrik operasional berikut:

- 100% run menggunakan immutable agent revision.
- 100% invocation memiliki `thread_id`.
- 100% revisi published lulus validator penuh.
- Run HITL dapat dilanjutkan setelah restart proses.
- Retry node tidak menjalankan ulang node hulu yang telah selesai.
- Tidak ada credential muncul pada stream, checkpoint-visible message, atau log.
- Agent sederhana dan root graph memakai endpoint execution serta format stream
  yang konsisten.

Target latency, biaya token, dan kualitas jawaban ditentukan setelah baseline
implementation dan evaluation dataset tersedia.

## Risiko dan mitigasi

### Perubahan API library

LangChain dan LangGraph berkembang cepat. Pin versi dependency dan bungkus hanya
seam yang diperlukan: model resolver, graph compiler, stream encoder, dan
checkpointer setup. Jangan membuat abstraksi paralel untuk seluruh framework.

### Retry menggandakan side effect

Task yang belum menyelesaikan checkpoint dapat diulang. Wajibkan idempotency key
untuk HTTP tool mutatif dan izinkan `max_attempts=1` untuk operasi yang tidak
dapat dibuat idempotent.

### Agent-as-tool menyembunyikan state sub-agent

LangGraph tidak dapat menemukan sub-agent yang dipanggil di dalam tool secara
statis. Larang HITL secara transitif dan arahkan user memakai node `ref` saat
introspeksi atau approval diperlukan.

### Context membesar tanpa batas

Gunakan `SummarizationMiddleware`, `ContextEditingMiddleware`, model context
profile, dan context policy eksplisit. Checkpoint tetap menyimpan history penuh.

### Konfigurasi menjadi bahasa pemrograman

Batasi routing mekanis pada satu perbandingan dan mapping tool pada pasangan
field. Jangan menambahkan expression language, Jinja, transform node, atau kode
user pada versi pertama.

### Resume menggunakan dependency yang berubah

Gunakan immutable revisions untuk agent, model, dan tool. Pin seluruh dependency
transitif pada publish.

## Urutan implementasi yang direkomendasikan

PRD ini tidak menetapkan ticket detail, tetapi implementasi harus bergerak dalam
tracer bullets berikut:

1. Bangun model revision, tool revision, agent revision, dan validator minimum.
2. Bangun compiler agent satu node dengan `ChatLiteLLM` dan `create_agent()`.
3. Tambahkan PostgreSQL checkpointer, conversation mode, run mode, dan stream
   encoder.
4. Tambahkan graph nodes, edges, structured output, dan named exits.
5. Tambahkan node `ref`, state privat, fan-out, dan `join: all`.
6. Tambahkan tool node, field mapping, dan generic `@task` wrapper.
7. Tambahkan conditional edges dan loop dengan `recursion_limit`.
8. Tambahkan HITL tool approval, node review, resume, dan retry API.
9. Tambahkan middleware policy, summarization, context editing, dan prompt
   context assembly.
10. Tambahkan `ChatLiteLLMRouter`, agent-as-tool, dan validator HITL transitif.
11. Lengkapi UI editor, run viewer, conversation upgrade, dan acceptance tests.

Setiap tracer bullet harus menghasilkan alur runnable dan test yang dapat
diamati, bukan hanya schema atau abstraksi kosong.

## Keputusan final

Dokumen ini mengunci keputusan berikut untuk versi pertama:

- Agent adalah data, bukan kode.
- Semua agent adalah graf; `create_agent()` sendiri merupakan compiled graph.
- Root dapat berupa agent tunggal atau `StateGraph` multi-agent.
- Node user hanya `agent` dan `tool`.
- Sub-agent `ref` memakai state privat dan boleh HITL.
- Agent-as-tool boleh digunakan, tetapi HITL dilarang secara transitif.
- Prompt memakai empat blok UI dan tiga field tersimpan.
- Model memakai LiteLLM in-process dan registry model seperti PINTER.
- Persistence memakai PostgreSQL checkpointer sebagai sumber kebenaran.
- Streaming memakai mode native LangGraph dengan `subgraphs=True`.
- HITL memakai `interrupt()` dan `Command(resume=...)` tanpa polling.
- Retry memakai native node retry dan `@task` checkpointing.
- Agent, model, dan tool memakai immutable revisions.
- Validasi dibagi menjadi autosave validation dan publish validation.
- Conversation dan orchestration memakai endpoint serta thread semantics berbeda.
- Disconnect membatalkan request, tetapi checkpoint dapat diteruskan melalui
  retry.
- Fan-in versi pertama hanya `join: all`.
- Background worker, long-term memory, eval, dan time-travel UI ditunda.

## Langkah berikutnya

Ubah PRD ini menjadi technical design yang menetapkan schema database, format
JSON setiap node dan edge, urutan middleware, compiler interfaces, endpoint
schemas, serta failure matrix. Setelah technical design disetujui, pecah
implementasi menjadi tracer-bullet tickets dengan dependency eksplisit.
