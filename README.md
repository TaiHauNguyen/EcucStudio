# EcucStudio

Tool cấu hình ECUC ARXML có giao diện kiểu DaVinci Configurator (Basic Editor), validate theo
rule của DaVinci, và gọi **DaVinci Configurator command line (DVCfgCmd.exe)** để validate/generate code.

## Chạy

```bat
run.bat D:\MyEcu\Project\MyEcu.dpa
rem hoặc
python -m ecucstudio gui D:\MyEcu\Project\MyEcu.dpa
```
Yêu cầu: Python 3.10+ (bản cài từ python.org, có chọn *tcl/tk and IDLE* và *Add python.exe to PATH*)
và gói `lxml` (gateway CAN-Ethernet cần thêm `cantools`):
```bat
py -m pip install -r requirements.txt
```
`run.bat` tự kiểm tra Python/tkinter/lxml, tự cài gói còn thiếu và in lỗi ra cửa sổ nếu không chạy được. Sau đó
nó khởi động GUI bằng đúng bản Python vừa kiểm tra (`pythonw.exe` cạnh `python.exe` của bản đó, không dùng alias
`pyw`/`pythonw`) và **chờ đến khi cửa sổ hiện trên màn hình** rồi in `[OK] … window shown at x,y`.

| Lệnh | Mở |
|---|---|
| `run.bat [project.dpa]` | EcucStudio |
| `run.bat gateway [gateway.json]` | CAN Gateway Generator (CAN-Ethernet, CAN-CAN) |
| `run.bat editor [network.arxml]` | CAN-Ethernet Gateway Editor |
| `run.bat debug …` | như trên nhưng chạy ngay trong cửa sổ console: mọi thông báo / lỗi hiện tại đó |

**Không thấy cửa sổ?**
- Sau 90 giây không có cửa sổ, `run.bat` in 20 dòng cuối của log `%LOCALAPPDATA%\EcucStudio\ecucstudio.log`
  và dừng lại để bạn đọc.
- Khi đó đóng `pythonw.exe` trong Task Manager (nếu còn chạy), rồi chạy `run.bat debug` để thấy lỗi trực tiếp.
- Cửa sổ luôn được đặt lên trên cùng. Vị trí đã lưu nằm ngoài màn hình (đóng lúc đang thu nhỏ, hoặc màn hình phụ
  đã rút ra) sẽ tự được đưa về giữa màn hình chính.
- Kiểm tra môi trường thủ công: `py -m ecucstudio --check`.

## Giao diện

Bố cục theo DaVinci Configurator 5 (icon tự vẽ, không dùng tài nguyên của Vector):

| Vùng | Chức năng |
|---|---|
| **Configuration Editors** (trái) | `<Filter>`, các domain gập/mở (Base Services, Communication, Diagnostics, I/O, Memory, Microcontroller, Mode Management, Network Management, Runtime System, Security, Time Synchronization); link mở editor theo module hoặc cả domain; *Basic Editor*, *Project Settings* ở đáy |
| **Editor area** (giữa, dạng tab) | Address line (breadcrumb) + trạng thái validation; cây ngữ cảnh có `<Filter>`; **form** tham số: nhãn (LONG-NAME nếu có) · ô nhập/combobox/checkbox · `dec`/đơn vị · ▾ menu (Set to default, Set/Remove user defined, Create/Delete parameter, Copy Path, Physical Units, Show target, Show properties) · biểu tượng lỗi. Chọn nhóm container (`ComIPdu [168]`) → **grid**. Section Sub-Containers có link *Add* |
| **Properties** (dưới trái) | Tab dọc Description / Status (Derived Value, Preconfigured, Default, Changeable, User defined, Annotations, Path, File) / Definition |
| **Validation · Find · Generation Result · Console** (dưới phải) | Validation: cột ID / Message, "N messages in M categories", nút Validate / DaVinci validation / Solve All và lọc Error/Warning/Info/Acknowledged trên thanh view; Find: tìm container, tên hoặc giá trị tham số; Generation Result: domain → generator → Generated Files |
| **Generate** (Ctrl+G) | Wizard: cây Generation Step có checkbox theo domain, cột Calculation / Validation / Generation cập nhật trực tiếp trong lúc DaVinci chạy, *Properties >>* để chọn DVCfgCmd, target, SWC templates, `--saveProject` |
| Status bar | Thông báo, tiến trình, số lỗi/cảnh báo, configuration phase |

Trạng thái tham số giống DaVinci: nhãn xám = *not set* hoặc read-only; icon khoá = *pre-configured*;
icon mắt xích = *derived* (sửa bằng *Set user defined*); icon người = *user defined*. Đơn vị hiển thị theo
`DV:Unit` (đổi bằng ▾ › Physical Units), luôn lưu theo `DV:BaseUnit`. Nút □ trên mỗi view để phóng to/thu nhỏ.

Phím tắt: Ctrl+O mở, Ctrl+S lưu, Ctrl+Z/Ctrl+Y undo/redo, Alt+←/→ editor trước/sau, F5 validate,
Ctrl+G generate, Ctrl+F Find, Ctrl+L đi tới đường dẫn (nhận cả `/ActiveEcuC/Com/ComGeneral[0:ComX]`),
F2 đổi tên, Del xoá container.

### Tạo cấu hình như DaVinci

| Việc | Cách làm | Tool làm gì |
|---|---|---|
| Thêm / bỏ module | *Project › Modules…* (hoặc link *Modules* trong Configuration Editors); bỏ: chuột phải module › *Remove Module* | Tạo `ECUC-MODULE-CONFIGURATION-VALUES` từ BSWMD: `DEFINITION-REF`, variant, `MODULE-DESCRIPTION-REF` (BSW implementation), container bắt buộc + default, áp **recommended** (`<Mod>_Rec`) rồi **pre-configuration** (`<Mod>_Pre`) của SIP; thêm vào `ECUC-VALUE-COLLECTION` của file ECUC chính |
| Thêm container | Chuột phải › *Create Sub-Container*, link *Add* ở Sub-Containers, nút + ở grid | Default + recommended configuration; tham số handle ID (symbolic name value) nhận **ID còn trống** kế tiếp |
| Thêm tham số | Gõ vào ô của tham số chưa đặt, hoặc ▾ › *Create parameter* (tham số nhiều instance) | Tạo value đúng thứ tự định nghĩa |
| Tạo phần bắt buộc còn thiếu | Chuột phải module/container › *Create Missing Mandatory Elements* | Tạo tham số bắt buộc (có default) và sub-container bắt buộc |

Tất cả đều Undo/Redo được. Đã kiểm chứng: module/container tạo bằng tool → lưu → `DVCfgCmd -g` sinh code
(vd `#define ComConf_ComIPduGroup_<Tên> 14u` trong `Com_Cfg.h`, `vSecPrim_Cfg.h`). Lỗi cấu hình thật (vd module
rỗng như `IpduM` không có PDU, `Rtm` cần bật measurement trong RTE) vẫn do người cấu hình quyết định — DaVinci
báo chúng khi validate.

### Mở project trên máy khác

Nếu đường dẫn SIP trong `.dpa` (thường `..\Core`) không tồn tại, tool hỏi thư mục SIP khi mở project.
Có thể đặt/đổi ở *Project › Project Settings › Definitions*: thư mục SIP riêng cho project, thư mục BSWMD bổ
sung, thư mục `StandardDefinition`. Không có SIP thì chỉ sửa được các giá trị đã có sẵn.

### Khi SIP thiếu định nghĩa (BSWMD)

Mọi module/container vẫn xem và sửa được, theo thứ tự nguồn định nghĩa:
1. BSWMD của SIP (gộp cả định nghĩa bị tách ra nhiều file — AUTOSAR "splitable");
2. định nghĩa chuẩn AUTOSAR của DaVinci (`<DaVinci>\Core\StandardDefinition`, lấy theo đường dẫn DVCfgCmd
   trong Settings), đổi gốc về đường dẫn `/MICROSAR/...` của ECUC nên file ghi ra vẫn đúng với DaVinci;
3. suy ra từ chính ECUC (`DEFINITION-REF DEST="ECUC-...-DEF"` cho biết kiểu tham số).

Form hiện dòng thông báo vàng cho biết nguồn đang dùng và lý do. `python -m ecucstudio diag <dpa>` liệt kê
mọi định nghĩa không tìm thấy trong SIP.

## Validation

1. **Basic rules** (tự cài đặt, cùng ID/nội dung với DaVinci): AR-ECUC02008 multiplicity,
   02027/02028 range, 02030/02031 linker symbol, 02039 đích reference sai định nghĩa, 02067 choice,
   02093 đích không active, 03005 enum, 03019 sai định nghĩa, 06052 variant, AR-BSWMD00033
   lệch pre-config, AR-BSWMD00034 lệch published information, AR-GST00021 trùng tên (không phân biệt hoa thường),
   Cfg00020 lệch cấu hình initial (container derived bị xoá, tham số khác giá trị derived),
   Cfg00021 sai kiểu, Cfg00022 thiếu giá trị, Cfg00024 thiếu đích reference, Cfg00028, Cfg00031, Cfg00032.
   Trên project thật dùng để phát triển, kết quả trùng khớp 100% với `DVCfgCmd -v` cho các ID này.
2. **Rule plugin** trong `rules\*.py` (xem `rules\README.md`): ví dụ EST10001 (Com PERIODIC không có
   period), EST20001 (stack task Os không chia hết 8), EST00002 (short name không hợp lệ).
3. **DaVinci thật**: nút *Validate with DaVinci* chạy `DVCfgCmd -v --reportFile ...` và gộp kết quả
   (COMxxxxx, RTExxxxx, …) vào cùng view; double-click để nhảy tới đối tượng.

Tham số User-Defined được hạ Error → Warning như DaVinci. Acknowledge của DaVinci (lưu trong .dpa)
được nhận diện; acknowledge của tool lưu ở `%APPDATA%\EcucStudio\settings.json` (không sửa .dpa).

## Generate code

Đường dẫn DVCfgCmd.exe: *Project › Settings…* (lưu ở `%APPDATA%\EcucStudio\settings.json`). Tool tự dò
`<SIP>\DaVinciConfigurator\Core\DVCfgCmd.exe` và các đường dẫn trong biến môi trường
`ECUCSTUDIO_DVCFGCMD` (phân cách bằng `;`), ưu tiên bản khớp phiên bản DaVinci của SIP.

*Project › Generate with DaVinci…*: chọn DVCfgCmd,
chọn module (hoặc tất cả), target REAL/VTT, SWC template, `--saveProject`. Tool lưu file, (tuỳ chọn)
validate cục bộ các module đã chọn, rồi chạy:
```
DVCfgCmd.exe -p <dpa> -g -m /MICROSAR/Det,... --reportFile <...>\GenerationReport.xml --reportArgs CreateXmlFile -l <log>
```
Output hiện trực tiếp ở Console; kết thúc thì đọc report → tab Generation Result (trạng thái từng
generator, file sinh ra, `FILE_IS_UP_TO_DATE`...). Report/log nằm ở
`%LOCALAPPDATA%\EcucStudio\reports\<project>` (thư mục project không bị ghi thêm).

Lưu ý môi trường của workspace này: MCAL (Adc, Dio, Mcu, Port, …) generate qua EB tresos cần license
tresos; MemMap cần `..\Board\AsrCommon\inc\MemMap_User.h`. Generate các module MICROSAR riêng lẻ vẫn chạy được.

## Command line

```bat
python -m ecucstudio info     Project.dpa
python -m ecucstudio diag     Project.dpa   :: giải thích lỗi "definition ... not found" (SIP, file BSWMD, phần thiếu)
python -m ecucstudio validate Project.dpa [--modules Com,Det] [--json out.json] [--davinci]
python -m ecucstudio set      Project.dpa "/ActiveEcuC/Det/DetGeneral[0:DetVersionInfoApi]" true --save
python -m ecucstudio generate Project.dpa -m /MICROSAR/Det [--gen-type REAL]
```
`validate` trả exit code 1 nếu còn Error chưa acknowledge (dùng được trong CI).

## Gateway CAN ↔ Ethernet và CAN → CAN (PduR)

Sinh **System Description** (file network ARXML) có gateway PduR giữa CAN và Ethernet, và giữa các bus CAN, để
import vào DaVinci Configurator (*Input Files*). Từ file này DaVinci tự suy ra PduR routing path, SoAd PduRoute /
SocketRoute (header ID) và CanIf PDU — không phải sửa ECUC bằng tay.

Hướng dẫn sử dụng đầy đủ: [docs/GATEWAY.md](docs/GATEWAY.md).

**CAN → CAN**: thêm nhiều DBC (mỗi DBC một bus, chọn node của ECU trong từng DBC). Message ECU nhận trên một bus
và gửi trên bus khác (cùng tên, tên có tiền tố `GW_`/`XGW_`…, hoặc cùng CAN ID và độ dài) được route thẳng giữa
hai bus nếu độ dài và layout signal khớp; route `ETH->CAN` của message đó tự tắt. Cặp khác độ dài / khác layout
/ nhận trên nhiều bus được liệt kê nhưng không route (ghép tay bằng *Add CAN -> CAN link…*). Chỉ cần CAN → CAN thì
tắt *CAN <-> Ethernet routes* ở tab Options, không cần thông số Ethernet. Chi tiết: mục 12 của
[docs/GATEWAY.md](docs/GATEWAY.md).

**Đã import file gateway vào DaVinci, giờ cần thêm/bớt DBC hoặc message?** Generator → **Open…** chính file
gateway `.arxml` (cấu hình được nhúng trong file; file của bản cũ được dựng lại từ nội dung), thay đổi, Generate
rồi **Update** trong DaVinci. Route giữ nguyên ra y hệt (tên, UUID, header ID), nên DaVinci giữ nguyên cấu hình
ECUC của chúng, kể cả tham số đã sửa tay; chỉ phần thêm/bớt thay đổi. Chi tiết: mục 7.1 của
[docs/GATEWAY.md](docs/GATEWAY.md).

File network **đã có gateway** (do tool sinh ra hoặc từ PREEvision): mở bằng **Gateway Editor** (nút *Edit
Existing Gateway…* của generator, menu *Tools → CAN-Ethernet Gateway Editor…*, hoặc `gateway editor <file>`) để
xem tất cả route, sửa header ID / socket connection / port / IP, xoá route (dọn luôn phần tử Ethernet chỉ route đó
dùng) và thêm route mới. Command line: `gateway routes` và `gateway edit`. Chi tiết ở mục 11 của
[docs/GATEWAY.md](docs/GATEWAY.md).

Mở: menu **Tools → CAN Gateway Generator (CAN-Ethernet, CAN-CAN)…**, `run.bat gateway`, hoặc
`python -m ecucstudio gateway gui [gateway.json]`. Cần thêm gói `cantools` (có trong requirements.txt).

**Input**

| Mục | Nội dung |
|---|---|
| Base system description | file network ARXML hiện có của project (ví dụ export từ PREEvision), **không bắt buộc**. Có Ethernet cluster thì gộp vào đó; chưa có (file chỉ có CAN) thì tool tạo cluster, kênh/VLAN, controller, connector và IP của ECU. Bỏ trống (chỉ có DBC) thì tool tạo file network mới hoàn toàn (ECU, SYSTEM, bus CAN, Ethernet, gateway). Tool tự dò ECU, kênh Ethernet/VLAN, connector, endpoint (IP), socket, header ID đang dùng, kênh CAN và cách chia package — không có tên cố định của project nào |
| Project DaVinci (.dpa) | thay cho DBC: chọn file `.dpa` của project đã import DBC; tool đọc message của ECU từ `Config\System\Communication.arxml` và tạo **file bổ sung** (Ethernet + gateway) để thêm vào Input Files, DBC giữ nguyên |
| DBC + node | node = ECU gateway trong DBC. Message node **nhận** → CAN→ETH, message node **gửi** → ETH→CAN, map 1:1. NM và diagnostic mặc định bỏ (bật được) |
| Kênh CAN | dùng kênh có sẵn trong base (frame tìm theo CAN ID, chỉ thêm port nếu thiếu) hoặc tạo CAN cluster mới từ DBC (baud rate từ DBC hoặc nhập) |
| Ethernet (nhập tay hoặc **Suggest values**) | VLAN (có sẵn hoặc tạo mới), connector của ECU, IP local; mỗi chiều chọn socket có sẵn hoặc nhập port local + IP/port remote để tạo socket mới; header ID set. Nút *Suggest values* / lệnh `gateway suggest` điền các ô trống từ nội dung file (kênh ECU đang dùng, IP trống kế tiếp, node đối tác, cặp port trống, MAC) kèm lý do |

**Header ID**: = CAN ID đệm 0 thành 32 bit (`0x123` → `0x00000123`). Nếu trùng với PDU khác được nhận trên
cùng socket (tính cả PDU có sẵn trong base) tool tự đặt cờ ở bit 29..31 (`k << 29`, k = 1..7 — CAN ID 29 bit
không dùng các bit này) và báo warning. Header ID nhập tay trong bảng route không bao giờ bị đổi, trùng thì
báo lỗi. Tùy chọn: luôn đặt bit 31 cho ID extended (kiểu `Can_IdType`).

**Output**: base + phần tử mới, phần còn lại của file giữ nguyên từng byte; kèm
`<output>_gateway_routes.csv`. Phần tử được ghi theo thứ tự schema AUTOSAR và theo phong cách của base
(package, UUID). Tên/UUID ổn định khi chạy lại; chạy lại trên chính file output thì route đã có được bỏ qua.

| Chiều | Phần tử tạo ra |
|---|---|
| CAN (mới) | CAN-FRAME, I-SIGNAL-I-PDU, I-SIGNAL, SYSTEM-SIGNAL, frame/PDU/signal triggering, FRAME-PORT/I-PDU-PORT trên connector CAN của ECU (+ CAN cluster/controller/connector nếu bus mới) |
| Ethernet | I-SIGNAL-I-PDU cùng độ dài (cùng layout signal, hoặc không signal), PDU-TRIGGERING + I-PDU-PORT trên connector Ethernet, SO-CON-I-PDU-IDENTIFIER (HEADER-ID), SOCKET-ADDRESS / STATIC-SOCKET-CONNECTION / NETWORK-ENDPOINT khi tạo mới |
| Gateway | I-PDU-MAPPING trong GATEWAY của ECU (tạo GATEWAY nếu chưa có), FIBEX-ELEMENTS của SYSTEM |

Sau khi import, DaVinci còn yêu cầu vài tham số chỉ có trong ECUC (giống mọi route import từ system
description): PDUR13200 `PduRPduLengthHandlingStrategy`, PDUR10510 `PduRDestPduDataProvision`,
SOAD01616/01698/01736 `SoAdTxIf…`, handle ID → dùng *Solve*.

```bat
python -m ecucstudio gateway inspect  --base network.arxml --dbc Body.dbc      :: ECU, VLAN, socket, node có trong file
python -m ecucstudio gateway template --base network.arxml --dbc Body.dbc --node GwEcu -o gateway.json
python -m ecucstudio gateway plan     gateway.json                             :: bảng route, header ID, warning
python -m ecucstudio gateway generate gateway.json [-o network_gw.arxml]
```
Cấu hình (`gateway.json`) lưu đường dẫn tương đối, các lựa chọn Ethernet, mẫu đặt tên và các route bị
tắt / header ID nhập tay, để chạy lại từ file base gốc.

## An toàn dữ liệu

- Chỉ ghi các file ECUC đã sửa; phần không sửa giữ nguyên từng byte (đã test trên 70 file của project).
- Ghi file mới qua file tạm rồi thay thế, tạo `.bak` (tắt được trong Settings).
- Cảnh báo nếu file bị chương trình khác (DaVinci) sửa sau khi mở; sau khi DaVinci chạy với
  `--saveProject` tool đề nghị nạp lại.
- Không sửa `.dpa`, BSWMD hay SIP.

## Kiến trúc

```
ecucstudio/
  arxml.py      XML helpers, ghi file giữ định dạng
  bswmd.py      kho định nghĩa BSWMD (index có cache, nạp lười, refined module, pre-config)
  project.py    đọc .dpa, model ECUC sửa tại chỗ + undo/redo, đổi tên cập nhật reference
  validation/   engine + basic rules (ID DaVinci)
  rulekit.py    API viết rule plugin
  davinci.py    dò DVCfgCmd, dựng command line, chạy nền, parse DaVinciExecutionReport
  session.py    gom project/definition/model/validation, trạng thái tham số
  gui/          tkinter: app, tree, editor, properties, validation_view, console, dialogs
  gateway/      gateway CAN <-> Ethernet: dbcread, base (dò file network), planner (route, header ID),
                writer (ghi ARXML theo thứ tự schema), xmlorder, report, suggest, dvproject (.dpa),
                existing (đọc / sửa gateway có sẵn), regen (sinh lại file đã import), cli, gui, editor_gui
rules/          rule plugin (*.py)
tests/          unittest trên bản sao project thật; tests/fixtures: dữ liệu tổng hợp cho gateway
```
Test: `set ECUCSTUDIO_TEST_DPA=<project>.dpa` rồi `python -m unittest discover -s tests -v`
(các test trên project thật sẽ được bỏ qua nếu không đặt biến này).
