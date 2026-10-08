# Hướng dẫn sử dụng: Gateway CAN ↔ Ethernet và CAN → CAN (PduR)

Tool sinh **System Description** (file network ARXML) có gateway PduR giữa CAN và Ethernet, và giữa các bus CAN
với nhau. Import file này vào DaVinci Configurator thì DaVinci tự tạo PduR routing path, SoAd PduRoute /
SocketRoute (header ID) và CanIf PDU, không phải cấu hình ECUC bằng tay.

- **CAN → Ethernet / Ethernet → CAN**: message ECU gateway nhận trên CAN được đưa lên Ethernet, message ECU gửi
  trên CAN được lấy từ Ethernet.
- **CAN → CAN** (mục 12): có nhiều DBC, mỗi DBC một bus. Message ECU gateway **nhận trên bus A** và **gửi trên
  bus B** được route thẳng từ bus A sang bus B.
- **Nhiều ECU trên một mạng Ethernet** (mục 13): các zone ECU khai báo chung trong một topology; message một ECU nhận
  trên CAN và ECU khác gửi trên CAN đi thẳng giữa hai ECU, hai đầu khớp header ID / IP / port.
- **Mạng zonal + routing table** (cửa sổ chính, phần *Bắt đầu nhanh* bên dưới): HPC + các ECU zonal, DBC của cả mạng,
  routing table của khách hàng; sinh file gateway của từng ECU.

Có hai cách dùng: **giao diện** (mục 1–7) hoặc **command line** (mục 8). Cả hai ra cùng một kết quả.
File network đã có gateway thì mở bằng **Gateway Editor** để xem và sửa lại (mục 11).

Tool dùng được cho bốn tình huống:

| Bạn đang có | Kết quả |
|---|---|
| File network ARXML **có** Ethernet cluster | gateway được gộp vào file đó (file mới = file cũ + gateway) |
| File network ARXML **chỉ có CAN** | tool tạo thêm phần Ethernet (mục 3.1) |
| **Chỉ có file DBC**, không có file network | tool tạo một file network ARXML **mới hoàn toàn** (mục 2.1) |
| **Project DaVinci (.dpa) đã import DBC** | tool đọc message từ project và tạo một **file bổ sung** (Ethernet + gateway) để thêm vào Input Files; DBC giữ nguyên trong project (mục 2.2) |

---

## Bắt đầu nhanh: cửa sổ chính (mạng zonal + routing table)

`run.bat gateway` (hoặc EcucStudio: menu *Tools → CAN Gateway (zonal network, routing table)…*) mở **cửa sổ chính**.
Dùng cho mạng Ethernet có một **HPC** (máy tính trung tâm) và các **ECU zonal**, mỗi ECU zonal có các bus CAN (file
DBC), kèm **routing table** (Excel) của khách hàng. Bốn trang theo thứ tự công việc; dải **Next step** luôn ghi việc
cần làm tiếp (nút **Show** mở đúng trang). Mọi thứ lưu trong một **file mạng** (`gateway_network.json`, *Save* /
*Open*); lần sau cửa sổ tự mở file cuối cùng.

| Trang | Làm gì |
|---|---|
| **1 Network** | các node Ethernet: một HPC và các ECU zonal (tên, MAC, IPv4, port); VLAN, netmask, một socket cho hai chiều |
| **2 CAN buses (DBC)** | *Add DBC files…* (chọn nhiều file một lần). Tool tìm node gateway và ECU của từng DBC (node trùng tên ECU, thuộc tính `ECU` của node, tên ECU là một từ của tên node, hoặc node đã gán ở DBC khác); double-click để sửa node / ECU / cột routing table. Dòng màu cam là chưa đủ |
| **3 Routing table** | file Excel (hoặc CSV) của khách hàng. Bảng *Network columns*: cột mạng nào là bus nào của ECU nào; bảng *Rows*: mỗi dòng đi đường nào (CAN → CAN trong một ECU, ECU → ECU qua Ethernet, LIN, lỗi …), có ô lọc |
| **4 Generate** | ECU cần sinh file, tên *ECU instance in DaVinci*, file ra; **Analyze**, **Generate**, **Message report** |

**Node mặc định**: *Save as my default nodes* lưu danh sách node (kèm vai trò HPC) vào cài đặt người dùng
(`%APPDATA%\EcucStudio\settings.json`), không vào file mạng; mạng mới (*New*) bắt đầu bằng danh sách đó, *Load my
default nodes* lấy lại. Đổi vai trò: chọn node → *Set as HPC*, hoặc *Edit…*.

**Quy tắc sinh route** (cố định):

| | Quy tắc |
|---|---|
| CAN → Ethernet | mọi message ECU zonal nhận trên CAN được gửi lên HPC. Dòng routing table có mạng nguồn (`S`) là bus của ECU này và mạng đích (`D`) là bus của ECU khác: message được gửi **thêm** thẳng tới ECU đó (một PDU Ethernet, một header ID, hai đích) |
| Ethernet → CAN | message ECU zonal gửi trên CAN lấy từ HPC; routing table route nó từ bus của ECU khác thì lấy từ ECU đó (cùng PDU, header ID với đầu gửi) |
| CAN → CAN | dòng routing table giữa hai bus của **cùng** một ECU: message → PduR, signal → Com signal gateway, ghi vào file `.vsde` (mục 12.1, 17) |
| dòng signal giữa hai ECU | ECU nguồn gửi nguyên message tới ECU đích; signal gateway ở ECU đích (PDU Ethernet → signal CAN) **chưa được sinh**, report ghi `not supported` |
| HW-Accelerator = 1 | vẫn route (bỏ tick ở trang 3 để để lại cho LLCE / PFE) |
| message không có trong bảng | chỉ lên HPC / chỉ lấy từ HPC; giữa các ECU tool **không** tự ghép theo tên |

Ví dụ: ZoneA có BusA, BusB; ZoneB có BusC; HPC là Central.

- dòng `Wheel`, `S` = BusA, `D` = BusC: file của ZoneA gửi Wheel tới ZoneB **và** Central; file của ZoneB lấy Wheel từ
  ZoneA;
- dòng `EngineData`, `S` = BusA, `D` = BusB: CAN → CAN trong ZoneA (`.vsde`);
- message `Extra` ZoneA nhận nhưng không có trong bảng: chỉ lên Central.

**Generate** ghi file gateway của ECU đích và file `.vsde` cạnh nó; cạnh file mạng ghi `<mạng>.lock.json` (header ID
của cả mạng: giữ cùng file mạng để ECU sinh lúc khác vẫn khớp), `<mạng>_contract.csv` (mọi PDU Ethernet: ai gửi, ai
nhận, header ID, IP:port) và report `<mạng>_message_paths.html` / `.csv` (có bảng *Routing table*). Trong DaVinci:
thêm file gateway và `.vsde` vào Input Files của project ECU đích (cạnh các DBC) **một lần**, rồi Update. Sinh lại thì
file được ghi đè, DaVinci giữ cấu hình của phần không đổi.

Trang 4 sau Analyze: ô đếm (CAN → HPC, CAN → ECU khác, HPC → CAN, ECU khác → CAN, CAN → CAN message / signal, dòng
bảng cần kiểm tra), bảng route của ECU đích (chuột phải: *Enable*, *Disable*, *Automatic*; lưu trong file mạng), thông
báo (lỗi của mọi ECU vì chúng chặn Generate, cảnh báo của ECU đích và của mạng).

*ECU instance in DaVinci*: tên ECU instance trong project DaVinci của ECU đích. Bộ convert DBC đặt theo thuộc tính
`ECU` của node gateway trong DBC, không có thì theo tên node; để trống là dùng giá trị đó.

Menu **Advanced**: cửa sổ topology (mọi thiết lập của file mạng, mục 13), generator cho một ECU (cửa sổ cũ, mục 1–7,
có cửa sổ Start bên dưới), Gateway Editor (mục 11). `run.bat gateway gateway.json` (cấu hình một ECU) hoặc
`python -m ecucstudio gateway gui --classic` mở thẳng generator cũ.

## Cửa sổ Start của generator một ECU (menu Advanced)

Generator một ECU (*Advanced → Generator for one ECU (classic)…*) mở cửa sổ **Start** (mở lại bằng nút **Start…**).

**Input và output**

| Input | Bắt buộc? | Ý nghĩa |
|---|---|---|
| File DBC của **cả mạng** (mọi bus CAN) | bắt buộc | tool tìm các ECU gateway từ tên node trong DBC; các ECU khác cho biết message đi đâu |
| Project DaVinci của ECU đích | tuỳ chọn | DBC của ECU đó đã import thành công trong project; tool đọc phần CAN từ project |
| File gateway làm trước đó của ECU đích | tuỳ chọn | để cập nhật / sửa file đó (gồm cả trường hợp có project) |

**Output**: luôn là **một file gateway ARXML** cho ECU đích, chỉ có phần Ethernet + gateway. Phần CAN chỉ được
tham chiếu tới cái DaVinci tạo khi import DBC, nên không bị trùng:

- có project: tool đọc tên phần tử thật trong `Config/System/Communication.arxml` của project;
- không có project: tool dùng tên mà DaVinci đặt khi import DBC (`/Cluster/<Bus>/CHNL/PT_<msg>`,
  `/Topology/HardwareComponents/<ECU instance>` …; đã kiểm chứng với DaVinci 5.24: file sinh chỉ từ DBC, thêm vào
  project đã import các DBC đó, Update tạo đủ routing path PduR, không trùng). Cần đúng **tên ECU instance** trong
  DaVinci (ô *ECU instance in DaVinci*).

**Ba điểm bắt đầu** (trang đầu), sau đó đi cùng các trang:

| Điểm bắt đầu | Dùng khi |
|---|---|
| *Start from the DBC files of the network* | lần đầu: thêm DBC của cả mạng |
| *Open the network file saved before* | DBC, ECU, IP đã lưu: chọn ECU (ví dụ ECU tiếp theo) và sinh file của nó |
| *Update a gateway file generated before* | sửa file gateway của một ECU: tool tự tìm file mạng và ECU của file đó |

Các trang:

1. **DBC files of the network**: mỗi DBC có cột *Gateway node* (tool gợi ý) và *ECU*. Các DBC cùng ECU được gộp,
   dòng tóm tắt ghi kết quả, ví dụ *→ 3 ECU(s): Z1 (Power), Z2 (Body), Z3 (Chassis, Sensor)*. Node gateway được gợi ý
   theo: node có trong mọi DBC → node có tên trong tên file DBC → tên kiểu gateway / zone (`GW`, `XGW_…`, `ZONE`,
   `Z1`, `ZC2` …) → node nhiều message nhất. Node đặt theo tên bus của cùng một ECU (`XGW_Body` trên bus Body,
   `XGW_Chassis` trên bus Chassis) được gộp thành ECU `XGW`; cột ECU sửa được (gõ cùng tên để gộp).
2. **ECUs, IP addresses and network file**: IP của từng ECU (lấy từ bảng **Ethernet nodes** nếu có tên trong bảng,
   không thì gợi ý theo VLAN hoặc theo IP có trong project), VLAN, **One socket per ECU for both directions** (mặc
   định bật), port cho ECU không có trong bảng, node trung tâm (node của bảng không phải ECU nào được đề xuất sẵn). Tất cả lưu trong **file mạng**
   (`gateway_network.json`) dùng chung cho mọi ECU, kèm file lock giữ header ID, nên file gateway của các ECU sinh ở
   các lúc khác nhau vẫn khớp nhau.
3. **ECU to generate the gateway file for**: chọn ECU đích; project DaVinci (tuỳ chọn; nếu project đã có file gateway
   của tool, tool hỏi có cập nhật file đó không); file gateway cũ (tuỳ chọn); file ra; *ECU instance in DaVinci* (khi
   không có project); phiên bản DaVinci (5.24 → AUTOSAR_00049, 5.31+ → AUTOSAR_00052).

**Finish** lưu file mạng và mở cửa sổ topology với ECU đích đã chọn (tab *Routes of the selected ECU*, *Message
paths*). **Generate** ghi file gateway của ECU đích (các ECU khác chỉ tham chiếu). Trong DaVinci: thêm file đó vào
Input Files của project ECU đích, cạnh các file DBC, rồi Update (file đã có trong Input Files thì chỉ Update).

Routing được suy ra từ DBC của cả mạng, ví dụ DBC Power có node Z1, Body có Z2, Chassis và Sensor có Z3:

- message từ Chassis sang Sensor: **nội bộ Z3** (CAN → CAN), không qua Ethernet;
- message từ Body sang Power: **Z2 → Z1 bằng Ethernet** (CAN → ETH trong file của Z2, ETH → CAN trong file của Z1,
  cùng header ID, IP, port);
- message ECU đích nhận mà không ECU nào cần: không route (liệt kê trong report *Not routed*), trừ khi bật node trung
  tâm.

**Thanh "Next step"** (dải xanh dưới thanh công cụ của generator) luôn ghi việc cần làm tiếp và có nút làm luôn việc
đó. Nút *Manual settings (generator window)* ở trang đầu: cấu hình một ECU với thông số Ethernet nhập tay (các mục
2–11 bên dưới).

## 0. Chuẩn bị

- Python 3.10+ cùng các gói `lxml` và `cantools` (`py -m pip install -r requirements.txt`); routing table dạng
  `.xlsx` cần thêm `openpyxl` (mục 17). `run.bat` tự kiểm tra và tự cài nếu thiếu.
- File network ARXML mà project đang dùng (ví dụ export từ PREEvision), **nếu có**. Có Ethernet cluster thì
  gateway được gộp vào cluster đó. **Chưa có** Ethernet cluster (file chỉ có CAN) thì tool tự tạo cluster, kênh
  (VLAN), controller, connector và địa chỉ IP của ECU, xem mục 3.1. **Không có file network** thì bỏ trống, xem
  mục 2.1.
- File DBC của các bus CAN cần gateway, và tên node của ECU gateway trong DBC. Encoding được tự nhận dạng:
  UTF-8 có/không BOM, UTF-16, cp1252 (mặc định của tool Vector).
- Thông tin Ethernet: VLAN, port UDP/TCP của ECU, IP và port của node bên kia (hoặc dùng socket đã có
  trong file network). Nếu tool phải tạo kênh mới: thêm IP của ECU (và MAC nếu muốn).

## 1. Mở tool

| Cách | Lệnh |
|---|---|
| Trong EcucStudio | menu **Tools → CAN Gateway Generator (CAN-Ethernet, CAN-CAN)…** |
| Chạy riêng | `run.bat gateway` (thêm đường dẫn `gateway.json` để mở cấu hình đã lưu) |
| Command line | `python -m ecucstudio gateway gui [gateway.json]` |

Không mở cấu hình nào thì cửa sổ **Start** hiện ra (mục *Bắt đầu nhanh* ở trên).

Thanh công cụ: **Start…**, **New**, **Open…**, **Save**, **Save As…** (cấu hình `.json`), **Analyze**,
**Generate network ARXML**. Dải **Next step** bên dưới ghi bước tiếp theo.

## 2. Tab **Input**

1. **Base system description**: chọn file network ARXML đang dùng. Tool đọc file và hiện tóm tắt
   (schema, số ECU, số kênh Ethernet và CAN).
2. **Output file**: file sẽ được tạo ra, mặc định `<base>_gateway.arxml`.
3. **Gateway ECU**: để `<auto>` nếu file chỉ có một ECU, hoặc chọn ECU làm gateway.
4. **Add DBC…**, mỗi bus CAN một dòng:

   | Trường | Ý nghĩa |
   |---|---|
   | DBC file | file DBC của bus |
   | Gateway node | node là ECU gateway trong DBC; danh sách hiện số message node đó nhận / gửi |
   | CAN channel | kênh CAN có sẵn trong base (tool tìm frame theo CAN ID và dùng lại), hoặc `<new CAN cluster from DBC>` để tạo bus mới |
   | Bus name | tên `{bus}` dùng khi đặt tên phần tử (mặc định: tên kênh hoặc `DBName` của DBC) |
   | Baud rate / CAN FD data baud rate | chỉ dùng khi tạo cluster mới; bỏ trống thì lấy từ DBC (mặc định 500000, FD 2000000) |
   | RX messages: CAN -> ETH | route các message node **nhận** lên Ethernet |
   | TX messages: ETH -> CAN | route các message node **gửi** từ Ethernet xuống CAN |
   | include NM / include diagnostic | mặc định bỏ message NM (`NmAsrMessage`) và diagnostic (`DiagRequest/Response/State`) |

   Sửa: double-click dòng hoặc **Edit…**; xoá: **Remove**.

### 2.1 Chỉ có file DBC (không có file network)

1. Để **trống** ô *Base system description*.
2. **Add DBC…** như trên. Tool tự đề xuất *Output file* là `<DBName>_network.arxml` cạnh file DBC.
3. **Gateway ECU**: tên ECU-INSTANCE của file mới. Mặc định là node gateway trong DBC; gõ tên khác nếu project
   DaVinci dùng tên ECU khác.
4. **Schema (new file)**: `AUTOSAR_00052` (mặc định, DaVinci 5.31). Với DaVinci 5.24 chọn `AUTOSAR_00049` hoặc
   cũ hơn.
5. Tab **Ethernet**: kênh tự chọn `<create new channel (VLAN)>`. Nhập **VLAN id**, **ECU IP**, rồi port/IP cho hai
   chiều như mục 3.

File mới gồm:

- ECU-INSTANCE `/Topology/HardwareComponents/<ECU>`;
- SYSTEM `/System/System` (CATEGORY ECU_EXTRACT);
- CAN cluster của bus (từ DBC);
- Ethernet cluster / kênh / controller / connector / IP;
- socket, header ID, GATEWAY;
- base type, PDU, signal.

Các package theo bố cục của PREEvision.

> **Lưu ý khi import vào DaVinci.** Bus CAN trong file mới **chỉ chứa các message được route**. Message bị tắt
> (NM, diagnostic, message bỏ chọn) và các message node không gửi/nhận đều không có trong file.
> - Nếu ECU chỉ làm gateway cho bus này: import file mới **thay cho** DBC. Đừng import thêm cùng DBC đó, vì bus
>   sẽ xuất hiện hai lần.
> - Nếu ECU còn cần các message khác của bus (NM, diagnostic, signal cho SWC): chế độ dùng chung với DBC đang
>   import chưa được hỗ trợ. Hãy báo để bổ sung.

### 2.2 Dùng project DaVinci đã import DBC (không đọc DBC)

1. Ô *Network file / DaVinci project*: chọn file **`.dpa`**. Project phải đã import DBC và Update xong. Tool đọc
   system description DaVinci đã gộp (`Config\System\Communication.arxml`, mục `OEMCommunicationExtract` trong
   `.dpa`), không sửa gì trong project. Dòng thông tin hiện ECU instance, số kênh CAN và đường dẫn file bổ sung.
2. **Gateway ECU**: tự lấy ECU instance của project.
3. **Add DBC…**: hộp thoại liệt kê các kênh CAN mà ECU nối vào, kèm số message ECU nhận / gửi (ví dụ
   `Body (/Cluster/Body/CHNL) - ECU receives 29, sends 14`). Chọn kênh, **không cần file DBC**. Tên `{bus}` mặc
   định là tên cluster. Mỗi kênh là một dòng.
4. Tab **Ethernet**: dùng kênh Ethernet có sẵn của project, hoặc `<create new channel (VLAN)>`. Nếu ECU chưa có IP
   trên kênh thì nhập **ECU IP**.
5. **Generate** → `<ECU>_CanEthGateway.arxml` (đề xuất đặt cạnh file `.dpa`).
6. Trong DaVinci: **Input Files → thêm file này** (system description, ECU instance của project), **giữ nguyên các
   DBC**, rồi **Update**.

Quy tắc của chế độ này:

- File bổ sung chỉ chứa phần tử mới: PDU / signal Ethernet, socket, header ID, I-PDU-MAPPING, endpoint,
  connector / kênh mới. Phần tử cha đã có (ECU, Ethernet cluster / channel, GATEWAY, package) được ghi dạng
  **khung chỉ có SHORT-NAME, không UUID**; DaVinci gộp theo đường dẫn. File **không có SYSTEM**, vì DaVinci tự
  dựng SYSTEM của project.
- Frame / PDU CAN không bị định nghĩa lại: I-PDU-MAPPING tham chiếu thẳng đường dẫn DaVinci đã tạo từ DBC
  (ví dụ `/Cluster/Body/CHNL/PT_<message>`).
- Message ECU nhận mà Com cũng dùng: route thành 1:N (CanIf → Com + SoAd). Remark: `also received by Com`.
- Message ECU **gửi từ Com** (DBC khai báo ECU là sender): route ETH→CAN mặc định **tắt** (`sent by Com of
  <ECU>`). Nếu bật thì PDU CAN có hai nguồn (Com + Ethernet). Chỉ bật khi Com không còn gửi PDU đó.
- Khi DBC thay đổi: Update project trong DaVinci trước, để `Communication.arxml` mới nhất, rồi Generate lại và
  thay file bổ sung.

## 3. Tab **Ethernet**

| Trường | Ý nghĩa |
|---|---|
| Ethernet channel (VLAN) | kênh / VLAN để gửi và nhận PDU gateway, hoặc `<create new channel (VLAN)>` (mục 3.1). Kênh mà ECU chưa nối vào có ghi chú `ECU not connected` |
| ECU connector | connector Ethernet của ECU trên VLAN đó (`<auto>` = connector đầu tiên; tool cảnh báo nếu có nhiều). `<create new>` khi ECU chưa nối vào kênh |
| Local endpoint (ECU IP) | địa chỉ IP của ECU cho socket mới (`<auto>` = endpoint của connector) |
| Protocol | UDP (mặc định) hoặc TCP (chọn TCP role CONNECT / LISTEN) |
| Header id set | nơi lưu các SO-CON-I-PDU-IDENTIFIER: `<auto>` = set đang dùng cho socket đã chọn (hoặc tạo `CanEthGateway_Ids`), chọn set có sẵn, hoặc gõ tên set mới |

### 3.0 Nút **Suggest values** (gợi ý thông số)

Bấm **Suggest values** (cạnh ô kênh Ethernet) để tool điền các ô **còn trống** của tab Ethernet. Giá trị bạn đã nhập
được giữ nguyên. Mỗi giá trị gợi ý được liệt kê kèm lý do ở khung thông báo. Kiểm tra lại rồi bấm Analyze.

| Thông số | Cách gợi ý |
|---|---|
| Kênh Ethernet | kênh duy nhất ECU đang nối; nếu nhiều kênh thì chọn kênh có nhiều PDU dùng SoAd header nhất; nếu ECU chưa nối kênh nào mà file chỉ có một kênh thì chọn kênh đó (tool tạo connector) |
| Kênh mới / VLAN id | ECU chưa nối kênh nào, hoặc đã chọn `<create new channel (VLAN)>`: VLAN = bội số 10 kế tiếp sau VLAN lớn nhất đang dùng (10 → 20; 20, 40, 60, 80 → 100). File chưa có VLAN nào thì để untagged |
| Connector | nếu ECU có nhiều connector trên kênh: connector có IP và nhiều socket nhất |
| ECU IP / netmask | chỉ khi ECU chưa có IP trên kênh: địa chỉ trống kế tiếp sau địa chỉ lớn nhất trong subnet đang dùng của kênh; kênh chưa có địa chỉ nào thì dùng `192.168.<VLAN>.1/24` (untagged: `192.168.1.1/24`) |
| IP node bên kia | node mà ECU đang có nhiều socket connection nhất trên kênh (ví dụ tester); kênh chỉ có một node khác thì chọn node đó; không có node nào thì lấy địa chỉ trống kế tiếp sau IP của ECU |
| Port | cặp port trống kế tiếp sau port lớn nhất đang dùng trên kênh: CAN→ETH = p, ETH→CAN = p+1; kênh chưa dùng port nào thì 50000 / 50001. Port phía node bên kia lấy cùng số |
| MAC | chỉ khi phải tạo controller mới: `02:00:` + 4 byte của IP ECU (địa chỉ locally administered) |

Command line: `gateway suggest gateway.json` in gợi ý ra màn hình; thêm `--write` để ghi vào các trường còn trống của
file. Lệnh `gateway template` tự điền gợi ý (tắt bằng `--no-suggest`).

### 3.1 Khung **New channel / ECU connection**

Chỉ dùng trong ba trường hợp (dòng chữ xám ở cuối khung cho biết trường hợp nào đang áp dụng):

1. File base **chưa có Ethernet cluster**: kênh tự chọn là `<create new channel (VLAN)>`, tool tạo cluster mới.
2. Chọn `<create new channel (VLAN)>` để thêm một **VLAN mới** vào cluster đã có.
3. Chọn một kênh có sẵn mà **ECU chưa nối vào**: tool tạo connector cho ECU trên kênh đó.

| Trường | Ý nghĩa |
|---|---|
| Cluster | cluster chứa kênh mới: `<auto>` = cluster duy nhất có sẵn, hoặc tạo cluster mới `EthernetCluster` nếu file chưa có |
| Channel name | tên kênh mới (bỏ trống = `Channel_VLAN<id>` hoặc `Channel_Untagged`) |
| VLAN id | 1..4094; **bỏ trống = kênh untagged**. Trùng VLAN đã có trong cluster thì báo lỗi (hãy chọn kênh đó) |
| ECU IP / netmask | **bắt buộc** khi tạo kênh hoặc connector mới: tạo NETWORK-ENDPOINT của ECU (IP tĩnh). Nếu IP đã có trong kênh thì dùng lại |
| Controller | controller Ethernet của ECU cho connector mới: `<auto>` = controller có sẵn của ECU (coupling port được thêm VLAN membership), hoặc tạo `CT_<ECU>_Eth` nếu ECU chưa có |
| MAC (new controller) | MAC-UNICAST-ADDRESS của controller mới (không bắt buộc) |

Tool tạo ra:

- ETHERNET-CLUSTER (khi chưa có);
- ETHERNET-PHYSICAL-CHANNEL (CATEGORY WIRED, VLAN với VLAN-IDENTIFIER nếu có);
- NETWORK-ENDPOINT của ECU;
- ETHERNET-COMMUNICATION-CONTROLLER (COUPLING-PORT có VLAN-MEMBERSHIP SENT-TAGGED hoặc SENT-UNTAGGED);
- ETHERNET-COMMUNICATION-CONNECTOR, có NETWORK-ENDPOINT-REFS, và được nối vào COMM-CONNECTORS của kênh.

Từ các phần tử này, DaVinci suy ra Eth controller (MAC), EthIf controller (`EthIfVlanId`), TcpIp controller,
local address (IP tĩnh, netmask) và nhóm socket SoAd.

### 3.2 Socket hai chiều

Hai khung cho hai chiều:

- **CAN -> ETH**: ECU gửi PDU lên Ethernet.
- **ETH -> CAN**: ECU nhận PDU từ Ethernet.

Trong mỗi khung:

| Trường | Ý nghĩa |
|---|---|
| Local socket | socket có sẵn của ECU, hoặc `<create new>` |
| new socket name / port | khi tạo mới: tên (bỏ trống = `SA_<ECU>_CanGw`, hai socket: `SA_<ECU>_CanGw_Tx` / `_Rx`) và **port của ECU** |
| Remote socket | socket có sẵn của node bên kia, hoặc `<create new>` |
| remote IP / port | khi tạo mới: **IP và port của node bên kia** (IP đã có trong VLAN thì dùng lại endpoint đó) |
| remote socket name | tên socket remote mới (bỏ trống = tên mặc định) |
| Socket connection name | tên STATIC-SOCKET-CONNECTION mới (bỏ trống = dùng connection có sẵn tới remote đó, hoặc `<local>_to_<remote>`) |

**One socket for both directions** (mặc định bật cho cấu hình mới): chiều ETH -> CAN dùng chính socket và socket
connection của chiều CAN -> ETH. Chỉ còn một khung **Socket**:

- socket của ECU `SA_<ECU>_CanGw` (một port), socket của node bên kia `SA_Remote_CanGw` (hoặc `SA_<peer>_CanGw`);
- một STATIC-SOCKET-CONNECTION cho mỗi node, chứa identifier của cả hai chiều. DaVinci tạo một socket connection
  group: PduRoute (gửi) và SocketRoute (nhận) dùng chung socket connection;
- header ID của hai chiều độc lập (gửi và nhận là hai bảng riêng trong SoAd).

Cấu hình tạo bằng phiên bản cũ (không có khoá `ethernet.one_socket`) giữ nguyên hai socket, sinh lại không đổi file.
Bỏ chọn ô này thì quay lại hai socket như cũ.

### 3.3 Nhiều node Ethernet (peers)

Mặc định mọi PDU đi tới node của hai khung ở mục 3.2 (ví dụ máy tính trung tâm). Khi một số message phải tới node
khác (ví dụ một zone ECU khác, đi thẳng qua switch), khai báo node đó trong khung **Ethernet peers**:

- **Name of the node above**: tên của node ở mục 3.2 (bỏ trống = `default`), hiện trong cột **Peer** của bảng route.
- **Add peer…**: tên node và socket hai chiều của nó, cùng cách điền như mục 3.2:
  - **CAN -> ETH (sent to this node)**: IP và port mà node đó **nhận**;
  - **ETH -> CAN (received from this node)**: IP và port mà node đó **gửi**.
  - Để local socket là `<create new>` với tên và port trống thì dùng chung local socket của node mặc định (ECU có
    một port gửi và một port nhận cho mọi node). Port được điền sẵn theo node mặc định.

Chọn node cho message trên bảng route (sau **Analyze**):

- **Double-click** một route: ô **Send to** (CAN->ETH, chọn được nhiều node) hoặc **Receive from** (ETH->CAN, một
  node).
- Chọn nhiều dòng cùng chiều → chuột phải → **Ethernet peers…**.

Message gửi tới nhiều node (1:N) có **một** PDU Ethernet và **một** header ID; header ID đó phải còn trống trên
socket gửi của ECU và trên socket nhận của mọi node đích. DaVinci tạo một SoAdPduRoute với một PduRouteDest cho mỗi
node (đã kiểm chứng với DaVinci 5.24). Phần tử tạo thêm cho mỗi node: NETWORK-ENDPOINT `NEP_<node>` (nếu IP chưa
có), socket remote `SA_<node>_CanGw_Rx` / `_Tx`, và STATIC-SOCKET-CONNECTION từ local socket tới đó.

### 3.4 Bus của ECU khác (mạng zonal)

Ví dụ đang sinh gateway cho zone Z2; mạng có các zone Z1, Z2, Z3 và ECU trung tâm (node mặc định, ví dụ `Central`) trên
cùng VLAN. Thêm DBC của bus thuộc zone khác vào **CAN buses** và chọn node của zone đó (ví dụ Z3):

- Nếu DBC đó **không có** node của ECU đang sinh (Z2), tool hiểu đó là **bus của Z3**: hộp thoại ghi rõ, không cần chọn
  kênh CAN của Z2, bảng bus ghi *to / from Z3 over Ethernet*. Không sinh CAN → CAN và không tạo phần tử CAN nào của
  bus đó.
- Tool hỏi **IP và port của Z3** (một Ethernet peer tên Z3, cùng kênh / VLAN với node mặc định; port điền sẵn theo
  node mặc định).
- Routing:
  - message Z2 nhận trên bus của nó và Z3 gửi trên bus của Z3 (cùng tên, tên bỏ tiền tố GW, hoặc cùng CAN ID + độ
    dài): CAN → ETH tới **Central và Z3** (mọi message zone nhận đều lên Central);
  - message Z2 gửi trên bus của nó và Z3 nhận trên bus của Z3: ETH → CAN **từ Z3**;
  - khác độ dài / layout signal: không route sang Z3 (WARNING).
- **WARNING** liệt kê các message vừa lên Central vừa sang zone khác; cột **Peer** của bảng route ghi `Central, Z3`.
- Dòng **INFO** tóm tắt cả hai chiều: `Z2 -> Z3 (CAN -> ETH): n message(s): …` và `Z3 -> Z2 (ETH -> CAN): m message(s): …`.
- Route mà Z3 cần / cấp được **bật** cả khi nó đang tắt vì là route mới lúc cập nhật file cũ (*not in the previous
  gateway file*) hoặc vì Com của ECU cũng gửi PDU đó (DBC import coi ECU là bên gửi; tool cảnh báo hai nguồn). Route
  bị tắt tay, NM / diagnostic, hoặc đã được cấp CAN → CAN nội bộ thì giữ nguyên.
- Chọn node peer cho message bằng tay (mục 3.3) thì tool giữ lựa chọn đó.

### 3.5 Một message Ethernet ra nhiều bus CAN (ETH → CAN 1:N)

Ví dụ HPC gửi `HpcCmd` (CAN ID 0x12C) cho zone ECU; zone ECU gửi message này trên cả BusA và BusB (cùng CAN ID, cùng
độ dài). Cùng CAN ID là **một** message, kể cả khi hai DBC ghi layout signal, cycle time hay CAN FD khác nhau:

- tool tạo **một** PDU Ethernet và **một** header ID (tên theo bus đầu tiên, ví dụ `HpcCmd_oBusA_Eth`, header
  `0x0000012C`); HPC chỉ gửi một lần;
- GATEWAY có hai `I-PDU-MAPPING` từ PDU-TRIGGERING Ethernet đó tới PDU-TRIGGERING của BusA và BusB; DaVinci tạo một
  PduR routing path SoAd → CanIf (BusA) + CanIf (BusB);
- bảng route: dòng bus đầu ghi *1:N: forwarded to BusA, BusB*, dòng còn lại ghi *1:N: Ethernet PDU of BusA/HpcCmd*,
  cùng PDU Ethernet và header ID;
- DBC ghi layout signal khác nhau: vẫn gộp, PduR chuyển nguyên byte; Remark ghi *1:N although the signal layout
  differs …* và có một WARNING chung nhắc kiểm tra DBC;
- cùng CAN ID nhưng khác độ dài, khác nguồn Ethernet, hoặc được chọn tên PDU Ethernet / header ID / 1:N off bằng tay:
  bus đó có PDU Ethernet và header ID riêng (thường thêm flag, ví dụ `0x200000D9`). Tool báo WARNING
  và ghi lý do vào cột **Header note** / **Remark**, ví dụ `flag 1 added (0x000000D9 used by …); not 1:N with
  BusA/X: length differs (BusA 8, BusB 12)`;
- tự chọn cho từng CAN ID: chuột phải một dòng ETH->CAN → **1:N with the same CAN id…**. Hộp thoại liệt kê signal
  của message trên từng bus (start bit, độ dài, byte order), signal khác layout tô màu cam. Chọn:
  - **One Ethernet PDU for all these buses (1:N)**: gộp (độ dài phải bằng nhau). PduR chuyển nguyên PDU, PDU
    Ethernet theo layout của bus đầu tiên;
  - **An own Ethernet PDU and header id per bus**: không gộp;
  - **Automatic**: như mặc định (gộp khi cùng CAN ID, độ dài và nguồn Ethernet).

  Lựa chọn lưu trong cấu hình (`messages.<tên>.fanout` = `true` / `false`), sinh lại vẫn giữ;
- mạng nhiều ECU (mục 13): zone ECU nhận message từ ECU khác và forward ra hai bus của nó cũng dùng một PDU, header
  ID theo ECU gửi;
- sinh lại giữ nguyên tên PDU và header ID. Bỏ chọn **ETH -> CAN 1:N** ở tab Options thì mỗi bus một PDU Ethernet
  như trước.

### 3.6 Bảng Ethernet nodes (giá trị mặc định)

Nút **Ethernet nodes…** (tab Ethernet của generator, thanh công cụ của cửa sổ topology, trang mạng của wizard) mở
bảng các node của mạng: **tên, MAC, IPv4, port base**. Ví dụ:

| Name | MAC address | IPv4 address | Port base |
|---|---|---|---|
| Central | 02:00:00:00:00:01 | 10.0.5.1 | 41100 |
| Z1 | 02:00:00:00:00:02 | 10.0.5.2 | 41200 |
| Z2 | 02:00:00:00:00:03 | 10.0.5.3 | 41300 |

Tool điền từ bảng vào **các ô còn trống** (không ghi đè giá trị đã nhập), theo tên node:

- ECU đang làm (tên ECU instance hoặc node trong DBC, kể cả node đặt theo bus như `Z1_Body`): IP, MAC (controller
  mới), port của socket;
- node mặc định và các peer: IP và port của node bên kia;
- topology: IP, MAC, port của từng ECU và peer;
- một socket: port = port base; hai socket: gửi từ port base, nhận trên port base + 1.

Việc điền chạy khi bấm **Analyze**, **Suggest values**, lưu bảng, tạo topology bằng wizard, và lệnh `template`.
Mỗi giá trị được điền có một dòng INFO "Filled from Ethernet nodes: …". Bảng lưu trong cài đặt người dùng
(`%APPDATA%\EcucStudio\settings.json`, khoá `gateway_nodes`), không nằm trong file project.

## 4. Tab **Options & Naming** (không bắt buộc)

- **CAN <-> Ethernet routes**: tạo route CAN ↔ Ethernet (mặc định bật). Tắt đi khi chỉ cần CAN → CAN: không cần
  điền tab Ethernet và file không có phần Ethernet nào.
- **CAN -> CAN routes**: ghép message giữa các bus (mục 12, mặc định bật).
  - **also pair renamed messages**: ghép cả message bị đổi tên nhưng cùng CAN ID và độ dài (mặc định bật).
- **ETH -> CAN 1:N**: message ECU gửi trên nhiều bus (cùng CAN ID, độ dài, layout signal) là **một** PDU Ethernet,
  được forward ra mọi bus đó (mục 3.5, mặc định bật).
- **ETH -> CAN: Com does not send…**: DBC import trong DaVinci, Com không gửi các message nhận từ Ethernet
  (file `.vsde`, mục 12.2, mặc định bật).
- **Set bit 31 for extended CAN ids**: luôn đặt bit 31 cho CAN ID extended (kiểu `Can_IdType`).
  Mặc định tắt, tức header = CAN ID đệm 0.
- **Ethernet PDU content**:
  - cùng layout signal với PDU CAN (mặc định, giống file PREEvision);
  - hoặc PDU không có signal.
- **Timing of CAN PDUs sent by the gateway**:
  - `event`: gửi khi nhận được từ Ethernet (mặc định);
  - hoặc lấy cycle time từ DBC.
- **Add new elements to the SYSTEM**: thêm phần tử mới vào FIBEX-ELEMENTS (mặc định bật).
- **Short-name patterns**: mẫu đặt tên. Dùng được các trường `{bus} {msg} {sig} {ecu} {node} {canid}
  {frame} {pdu} {signal} {triggering} {connector} {eth_pdu}`; mẫu cho phần Ethernet mới (cluster, channel,
  controller, connector, endpoint) dùng `{ecu} {vlan} {vlan_id}` với `{vlan}` = `VLAN<id>` hoặc `Untagged`.
  Mặc định giống converter DBC của Vector, ví dụ:
  - frame / PDU CAN: `{msg}_o{bus}`
  - PDU Ethernet: `{msg}_o{bus}_Eth`
  - triggering: `{pdu}_PT`
  - kênh / connector Ethernet mới: `Channel_{vlan}` / `CN_{ecu}_{vlan}`

## 5. **Analyze**: xem và chỉnh route

Bảng route (một dòng là một message):

| Cột | Ý nghĩa |
|---|---|
| Enabled | `yes` / `no` (dòng xám là route bị tắt) |
| Direction | `CAN->ETH`, `ETH->CAN` hoặc `CAN->CAN` (cột Bus ghi `Body -> Chassis`) |
| CAN ID, Frame, Length, Cycle ms | thông tin từ DBC (hoặc từ base nếu frame đã có) |
| CAN PDU / Ethernet PDU | tên PDU hai đầu gateway |
| Peer | node Ethernet nhận (CAN->ETH, có thể nhiều node) hoặc gửi (ETH->CAN) PDU, mục 3.3 |
| Header ID | SoAd header ID (dòng cam = đã tự thêm cờ vì trùng) |
| Remark | lý do tắt, frame dùng lại từ base, 1:N / N:1, chênh lệch độ dài… |

Thao tác trên bảng:

- **Double-click**: bật/tắt route, nhập header ID tay (ví dụ `0x123`), đổi tên PDU Ethernet.
- **Space**: đảo bật/tắt các dòng đang chọn.
- **Chuột phải**: Enable / Disable / Edit… / Ethernet peers… (khi có nhiều node) / Add CAN -> CAN link… / Reset
  overrides.

Mọi thay đổi trên bảng được lưu vào cấu hình và áp dụng lại mỗi lần Analyze.

Khung thông báo bên dưới:

- **ERROR**: chưa generate được (ví dụ thiếu port, IP sai, header ID nhập tay bị trùng).
- **WARNING**: vẫn generate được, nhưng cần đọc. Ví dụ: header ID bị thêm cờ, ECU có nhiều connector,
  route N:1, độ dài PDU trong DBC khác trong base.
- **INFO**: ví dụ dùng lại kênh CAN cùng tên.

### Quy tắc header ID

- Header ID = CAN ID đệm 0 thành 32 bit: `0x123` → `0x00000123`, `0x18FF1234` → `0x18FF1234`.
- Header ID chỉ cần duy nhất ở **phía nhận** trên cùng socket:
  - chiều CAN→ETH: so với các PDU đang gửi từ socket local đó, và các PDU socket remote đang nhận;
  - chiều ETH→CAN: so với các PDU socket local đang nhận;
  - tính cả các PDU có sẵn trong file base.
- Nếu trùng: tool đặt cờ `k << 29` (k = 1..7, ở bit 29..31 mà CAN ID 29 bit không dùng), lấy giá trị nhỏ nhất
  còn trống, rồi báo warning. Ví dụ `0x100` đã có → `0x20000100`; nếu cả giá trị đó cũng đã có → `0x40000100`.
- Header ID nhập tay không bao giờ bị đổi; nếu trùng thì báo ERROR.

## 6. **Generate network ARXML**

Tool tạo hai file:

- **file output**: file base cộng các phần tử mới. Phần còn lại của file giữ nguyên từng byte, phần tử mới
  được ghi đúng thứ tự schema AUTOSAR.
- **`<output>_gateway_routes.csv`**: bảng route (mở bằng Excel), dùng để kiểm tra hoặc gửi cho người khác.

Nếu output trùng với file base, tool hỏi lại và giữ bản `.bak`.

Cấu hình được nhúng luôn trong file output. Bấm **Save** nếu muốn có thêm file `gateway.json`. Muốn thêm/bớt DBC
hoặc message sau khi đã import vào DaVinci: **Open…** chính file output rồi Generate lại (mục 7.1).

### Phần tử được tạo

| Phần | Phần tử |
|---|---|
| CAN (frame mới) | CAN-FRAME, I-SIGNAL-I-PDU, I-SIGNAL, SYSTEM-SIGNAL, CAN-FRAME-TRIGGERING, PDU-TRIGGERING, I-SIGNAL-TRIGGERING, FRAME-PORT / I-PDU-PORT trên connector CAN của ECU |
| CAN (bus mới) | thêm CAN-CLUSTER + kênh, CAN controller và connector trong ECU |
| CAN (frame có sẵn) | chỉ thêm FRAME-PORT / I-PDU-PORT nếu ECU chưa có |
| Ethernet | I-SIGNAL-I-PDU cùng độ dài, PDU-TRIGGERING + I-PDU-PORT trên connector Ethernet, SO-CON-I-PDU-IDENTIFIER (HEADER-ID), tham chiếu trong STATIC-SOCKET-CONNECTION, và SOCKET-ADDRESS / NETWORK-ENDPOINT / STATIC-SOCKET-CONNECTION khi tạo mới |
| Ethernet (kênh mới, mục 3.1) | ETHERNET-CLUSTER, ETHERNET-PHYSICAL-CHANNEL + VLAN, NETWORK-ENDPOINT của ECU, ETHERNET-COMMUNICATION-CONTROLLER + COUPLING-PORT, ETHERNET-COMMUNICATION-CONNECTOR |
| Gateway | I-PDU-MAPPING trong GATEWAY của ECU (tạo `Gateway_<ECU>` nếu chưa có) |
| System | tham chiếu phần tử mới trong FIBEX-ELEMENTS |

## 7. Import vào DaVinci Configurator

1. Trong **Input Files** của project:
   - có file network cũ: thay file đó bằng file output (hoặc ghi đè file cũ như ở mục 6);
   - file mới tạo từ DBC (mục 2.1): thêm file vào danh sách system description và gán ECU instance là tên ECU
     đã chọn. Không import lại DBC của bus đó.
2. Chạy **Update** project. DaVinci tạo:
   - **PduR**: `PduRRoutingPath` tên `Gateway_<ECU>_…`, nối CanIf ↔ SoAd;
   - **SoAd**: `SoAdPduRoute` (`SoAdTxPduHeaderId`), `SoAdSocketRoute` (`SoAdRxPduHeaderId`),
     socket connection group bật `SoAdPduHeaderEnable`;
   - **CanIf**: RX / TX PDU với CAN ID và kiểu ID (Standard / Extended, FD).
3. **Solve** các tham số chỉ có trong ECUC (giống mọi route import từ system description):

   | Rule | Tham số |
   |---|---|
   | PDUR13200 | `PduRPduLengthHandlingStrategy` của routing path IF không buffer |
   | PDUR10510, CANIF10034 | `PduRDestPduDataProvision = PDUR_DIRECT` cho đích CanIf |
   | SOAD01616 / 01698 / 01736 | `SoAdTxIfTriggerTransmit`, `SoAdTxIfOptimized`, `SoAdTxIfOptimisticTransmit` |
   | SOAD01502 | handle ID (`SoAdRxPduId`) |

4. Validate rồi generate như bình thường.

### 7.1 Thay đổi sau khi đã import: thêm / bớt DBC hoặc message rồi sinh lại

Dùng khi file gateway đã được import vào DaVinci và bạn muốn thêm/bớt bus (DBC, kênh CAN) hoặc thêm/bớt message.
DaVinci phải **giữ nguyên** phần không đổi, kể cả các tham số ECUC bạn đã sửa hoặc Solve, và chỉ cập nhật phần
thay đổi.

1. Generator → **Open…** → chọn **chính file gateway `.arxml`** đã import vào DaVinci.
   - File sinh bởi bản tool hiện tại mang sẵn cấu hình của nó: base/`.dpa`, DBC, node, Ethernet, message đã
     chọn. Tool khôi phục y nguyên.
   - File sinh bởi bản cũ chưa có cấu hình nhúng: tool tự dựng lại từ nội dung file (kênh/VLAN, connector, IP,
     socket, port, header ID set, các kênh CAN). Tool cũng tự tìm file `.dpa` đang import file đó. Chỉ những
     message đã có trong file được bật (tuỳ chọn *Regeneration: … route only its messages* ở tab Options).
   - Ô **Previous gateway file** và **Output file** đều là file đó.
   - Tool tự Analyze. Cột **Change** cho biết `kept` (giữ nguyên), `new` (mới, chữ xanh). Dòng `removed` (chữ đỏ)
     là route có trong file cũ nhưng sẽ không sinh nữa.
2. Thay đổi như bình thường:
   - thêm bus: **Add DBC…** (chế độ project: chọn kênh CAN; nếu là DBC mới thì import DBC vào DaVinci trước, hoặc
     chọn file DBC);
   - bớt bus: **Remove**;
   - bật/tắt message: bảng route (double-click, Space, chuột phải).
3. **Generate network ARXML** ghi đè file gateway, bản cũ giữ là `.bak`.
4. DaVinci: file đã nằm sẵn trong Input Files → chạy **Update**.

Tool đảm bảo route giữ nguyên ra **y hệt** lần trước:

- Phần tử của file cũ được trừ khỏi base trước khi tính. Ở chế độ project, `Communication.arxml` của DaVinci đã
  chứa chúng sau lần import trước; nếu không trừ, tool sẽ coi các route đó là "đã có sẵn".
- Route giữ nguyên dùng lại **đúng tên PDU Ethernet** và **header ID cũ**, kể cả header ID có cờ chống trùng. Vì vậy
  đường dẫn và UUID không đổi. Route mới không được dùng các header ID đó.
- Không thay đổi gì thì file sinh lại giống từng byte.

Đã kiểm chứng bằng DaVinci 5.24 trên một bản copy project (bỏ 2 message, thêm 1 bus CAN có 22 message):

- route giữ nguyên giữ nguyên tên container PduR / SoAd và header ID;
- tham số sửa tay `SoAdTxIfTriggerTransmit` trên route giữ nguyên vẫn còn sau Update;
- container của 2 route bị bỏ đã bị xoá, 22 route mới được tạo.

Cấu hình được nhúng trong ADMIN-DATA/SDGS ở gốc file (`SDG GID="EcucStudio.CanEthGateway"`); DaVinci bỏ qua phần
này. Đường dẫn trong cấu hình được ghi tương đối so với file gateway. Nếu chuyển máy mà base hoặc DBC không còn
ở chỗ cũ, tool báo để chọn lại.

## 8. Command line

```bat
:: 1. xem file base có ECU, VLAN, connector, IP, socket nào; DBC có node nào
python -m ecucstudio gateway inspect --base network.arxml --dbc Body.dbc

:: 2. tạo file cấu hình mẫu
python -m ecucstudio gateway template --base network.arxml --dbc Body.dbc --node GwEcu --channel VLAN60 -o gateway.json
:: project DaVinci đã import DBC: --base là file .dpa, mỗi bus là một kênh CAN của project (sửa "buses" trong json)
python -m ecucstudio gateway inspect --base MyEcu.dpa
:: chỉ có DBC, không có file network (thêm --ecu <tên> nếu ECU instance khác tên node, --schema AUTOSAR_00049 cho DaVinci 5.24)
python -m ecucstudio gateway template --dbc Body.dbc --node GwEcu --vlan 20 --ecu-ip 10.0.20.1 -o gateway.json
:: file base chưa có Ethernet (hoặc thêm VLAN mới với --new-channel): cho VLAN và IP của ECU
python -m ecucstudio gateway template --base can_only.arxml --dbc Body.dbc --node GwEcu --vlan 20 --ecu-ip 10.0.20.1 -o gateway.json

:: 3. template đã điền gợi ý Ethernet (mục 3.0); kiểm tra / sửa gateway.json (mục 9). Gợi ý lại các ô còn trống:
python -m ecucstudio gateway suggest gateway.json --write
:: xem trước kết quả (không ghi file)
python -m ecucstudio gateway plan gateway.json

:: 4. ghi file network mới + CSV (exit code 1 nếu còn ERROR)
python -m ecucstudio gateway generate gateway.json [-o network_gw.arxml] [-v]

:: sinh lại file gateway đã import vào DaVinci (mục 7.1): plan / generate nhận thẳng file .arxml
python -m ecucstudio gateway plan     GwEcu_CanEthGateway.arxml      :: kept / new / removed
python -m ecucstudio gateway reopen   GwEcu_CanEthGateway.arxml -o gateway.json   :: sửa buses / messages trong json
python -m ecucstudio gateway generate gateway.json
```

## 9. Tham khảo file cấu hình `gateway.json`

```json
{
  "base": "network.arxml",
  "output": "network_gateway.arxml",
  "ecu": "",
  "schema": "AUTOSAR_00052",
  "buses": [
    {
      "dbc": "Body.dbc",
      "node": "GwEcu",
      "channel": "",
      "new_channel": false,
      "bus": "",
      "baudrate": null,
      "fd_baudrate": null,
      "rx": true,
      "tx": true,
      "include_nm": false,
      "include_diag": false,
      "messages": {
        "DoorStatus": {"enabled": false},
        "EngineData": {"header_id": "0x1100", "eth_pdu": "EngineData_Eth"},
        "BrakeStatus": {"eth_peers": ["Central", "ZoneB"]},
        "GwCommand": {"eth_peer": "ZoneB"}
      }
    }
  ],
  "ethernet": {
    "channel": "VLAN60",
    "connector": "",
    "local_endpoint": "",
    "protocol": "UDP",
    "id_set": "",
    "new_channel": false,
    "cluster": "",
    "vlan_id": null,
    "channel_name": "",
    "ecu_ip": "",
    "ecu_netmask": "255.255.255.0",
    "controller": "",
    "mac": "",
    "can_to_eth": {"local_port": 50100, "remote_ip": "10.0.10.2", "remote_port": 50100},
    "eth_to_can": {"local_socket": "SA_GwEcu_Rx", "remote_socket": "SA_Tester_Tx"},
    "default_peer": "Central",
    "peers": [
      {"name": "ZoneB",
       "can_to_eth": {"remote_ip": "10.0.10.3", "remote_port": 50100},
       "eth_to_can": {"remote_ip": "10.0.10.3", "remote_port": 50101}}
    ]
  },
  "header": {"extended_flag": false, "flag_shift": 29},
  "options": {"eth_signals": "copy", "can_tx_timing": "event", "add_fibex": true,
              "eth_routes": true, "can_routes": true, "can_match_id": true},
  "can_gateway": {"Body/DoorStatus->Chassis/DoorStatus": {"enabled": false}},
  "can_links": [{"src_bus": "Body", "src_msg": "EngineData", "dst_bus": "Chassis", "dst_msg": "EngData_Fwd"}]
}
```

- Đường dẫn tương đối tính từ thư mục chứa file `.json`.
- `base` là file `.dpa` = dùng project DaVinci (mục 2.2). Mỗi bus là `{"channel": "/Cluster/Body/CHNL"}` (hoặc tên
  cluster), không có `dbc` / `node`. Output là file bổ sung.
- `base` để trống = chỉ có DBC: tạo file mới. Khi đó `ecu` là tên ECU-INSTANCE mới (trống = node của DBC đầu
  tiên), `schema` là schema của file mới.
- ECU, kênh, connector, socket có thể ghi bằng **đường dẫn AUTOSAR** hoặc **tên ngắn**. Kênh Ethernet còn ghi
  được dạng `VLAN60`. Trường bỏ trống là tự dò.
- Mỗi chiều trong `ethernet`:
  - dùng socket có sẵn: `local_socket` + `remote_socket`;
  - tạo mới: `local_port` + `remote_ip` + `remote_port` (thêm `local_name`, `remote_name`,
    `remote_endpoint`, `remote_netmask`, `connection_name` nếu muốn).
- Kênh mới / ECU chưa nối (mục 3.1): `new_channel`, `cluster`, `vlan_id` (`null` = untagged), `channel_name`,
  `ecu_ip`, `ecu_netmask`, `controller`, `mac`. Base không có Ethernet thì tự tạo kênh, chỉ cần `ecu_ip`
  (và `vlan_id` nếu là VLAN).
- `messages`: chỉnh từng message theo tên trong DBC. `enabled` tắt/bật route, `header_id` đặt header ID tay,
  `eth_pdu` đổi tên PDU Ethernet, `eth_peers` (CAN->ETH, danh sách) / `eth_peer` (ETH->CAN) chọn node Ethernet.
- `default_peer` / `peers` (mục 3.3): tên node của `can_to_eth` / `eth_to_can`, và các node khác. Trường local
  của một peer bỏ trống = dùng chung local socket của node mặc định.
- `naming` (không ghi thì dùng mặc định): các mẫu tên như ở tab Options & Naming.
- CAN → CAN (mục 12): `options.eth_routes` / `can_routes` / `can_match_id`; `can_gateway` bật/tắt từng cặp theo
  khoá `<bus nguồn>/<message>-><bus đích>/<message>` (khoá hiện trong bảng route); `can_links` thêm cặp tay.

## 10. Lỗi thường gặp

| Thông báo | Cách xử lý |
|---|---|
| `… Communication.arxml does not exist` | project chưa import DBC / chưa Update: import DBC trong DaVinci, Update, lưu project |
| Editor: `Header id 0x… is already used on the same socket by …` | chọn giá trị khác, hoặc đổi header ID của PDU kia trước |
| `select the CAN channel of the project` | chọn kênh CAN trong hộp thoại bus (chế độ project) |
| DaVinci: `UUID is not unique in these two files` hoặc `Duplicate shortname 'System'` | file bổ sung tạo bằng phiên bản cũ của tool: cập nhật (`git pull`) và Generate lại |
| `Select the output file (there is no base file)` | chưa chọn *Output file* khi không có file base |
| `Enter the IP address of <ECU> on <channel>` | tool cần tạo kênh / connector mới: nhập **ECU IP** ở khung New channel / ECU connection |
| `… already has VLAN <n> (…)` | VLAN đó đã có: chọn kênh có sẵn thay vì `<create new channel (VLAN)>` |
| `the remote IP … is the IP address of <ECU>` | IP remote trùng IP của ECU: nhập IP của node bên kia |
| `several Ethernet clusters` | chọn **Cluster** cho kênh mới |
| `Select the gateway ECU` | file có nhiều ECU: chọn **Gateway ECU** |
| `Select the Ethernet channel (VLAN)` | ECU nối với nhiều VLAN: chọn kênh ở tab Ethernet |
| `enter the local UDP port` / `enter the remote UDP port` / `enter the remote IP address` | nhập đủ port, IP cho socket mới, hoặc chọn socket có sẵn |
| `the connector has no network endpoint` | chọn **Local endpoint** (IP của ECU) |
| `header id 0x… is already used by …` (ERROR) | header ID nhập tay bị trùng: đổi giá trị hoặc xoá để tool tự gán |
| `node '…' is not in …` | tên node không có trong DBC: chọn lại ở hộp thoại DBC |
| `… is not a valid DBC file: …` | file không phải DBC hoặc bị hỏng; xem dòng/cột trong thông báo. (Bản cũ báo `Invalid syntax at line 1, column 1: ">>!<<ï»¿VERSION"` với DBC lưu kèm BOM UTF-8 — đã sửa, cập nhật tool bằng `git pull`) |
| Warning `remote endpoint … is an address of <ECU> itself` | IP remote đang là IP của chính ECU: nhập IP của node bên kia |
| Warning `… N:1 route` | PDU CAN đó đã là đích của một route khác; N:1 chỉ có trong MICROSAR dạng extension, tắt route nếu không cần |

## 11. Sửa gateway trong file network đã có (Gateway Editor)

Mở một file network ARXML **đã có gateway** để xem và sửa trực tiếp từng phần tử (header ID, socket, port, IP,
xoá route). Muốn thêm/bớt DBC hoặc message rồi sinh lại file gateway thì dùng mục 7.1. File có thể do tool sinh ra, do PREEvision
xuất, hoặc là file bổ sung của chế độ project.

Mở bằng một trong các cách sau:

- Generator: nút **Edit Existing Gateway…**. Tool mở file output nếu file đã tồn tại, nếu không thì mở file base,
  nếu không có cả hai thì hỏi chọn file.
- EcucStudio: menu **Tools → CAN-Ethernet Gateway Editor…**.
- Command line: `python -m ecucstudio gateway editor network.arxml`.

### 11.1 Tab **Routes**

Liệt kê mọi route của các GATEWAY trong file (CAN→ETH, ETH→CAN, CAN→CAN, ETH→ETH), gồm các cột:

- bus CAN, frame, CAN ID;
- kênh Ethernet, PDU Ethernet, độ dài;
- header ID, socket connection;
- Remark: 1:N / N:1, SecOC, PDU không có header, phía route nằm ngoài file.

Lọc theo chiều (Direction), theo gateway, hoặc theo tên (Filter: tìm trong tên PDU, frame, triggering, header ID).
Bấm tiêu đề cột để sắp xếp.

- **Double-click / Edit…**: sửa **header ID** và **socket connection** của route.
  - Header ID nhập dạng `0x1A2B` hoặc số thập phân.
  - Tool kiểm tra trùng như khi generate: duy nhất trên socket nhận (chiều ETH→CAN), và trên socket gửi lẫn socket
    nhận phía bên kia (chiều CAN→ETH). Trùng thì báo lỗi và không đổi gì.
  - Socket connection chỉ được chọn trong các connection của connector đang gửi/nhận PDU đó.
- **Delete Routes… / phím Delete** (chọn được nhiều dòng):
  - Xoá I-PDU-MAPPING của route.
  - Mặc định xoá luôn phần tử phía Ethernet **chỉ** route đó dùng: PDU-TRIGGERING, I-SIGNAL-I-PDU (cả
    SECURED-I-PDU), I-SIGNAL, I-SIGNAL-TRIGGERING, I-PDU-PORT / I-SIGNAL-PORT, SO-CON-I-PDU-IDENTIFIER cùng tham
    chiếu của nó trong socket connection, FIBEX-ELEMENT-REF và mục trong I-SIGNAL-I-PDU-GROUP.
  - Phần tử còn route khác dùng thì giữ lại. Ví dụ: PDU Ethernet 10:1 chỉ mất một nguồn.
  - **Phía CAN giữ nguyên** (frame, PDU, signal thuộc database của bus).
  - Bỏ tick ô tuỳ chọn trong hộp thoại xoá để chỉ xoá mapping.
- **Add Routes…**: mở generator với **chính file này làm base và output** để thêm route từ DBC hoặc project
  (route đã có được bỏ qua). Generate xong, quay lại editor: file được tự nạp lại.

### 11.2 Tab **Sockets** và **Endpoints**

- **Sockets**: kênh, socket, chủ sở hữu (ECU / remote), IP, port, giao thức, các socket connection, số header ID.
  Double-click để sửa **port**; không cho trùng port cùng giao thức trên cùng địa chỉ.
- **Endpoints**: kênh, endpoint, chủ sở hữu, IP, netmask. Double-click để sửa **IP / netmask**; không cho trùng IP trên
  cùng kênh.

### 11.3 Lưu

- **Save** ghi đè file và giữ bản cũ là `.bak`; **Save As…** ghi ra file khác.
- Tiêu đề cửa sổ có `*` khi còn thay đổi chưa lưu. Đóng cửa sổ hoặc mở file khác khi chưa lưu thì tool hỏi lại.
- Phần không sửa của file giữ nguyên từng byte. **Reload** bỏ các thay đổi chưa lưu.
- Khi mở file, tool kiểm tra và báo trong khung log:
  - header ID trùng trên cùng socket;
  - header ID không gắn socket connection nào;
  - header ID trỏ tới PDU-TRIGGERING không có trong file.

### 11.4 Command line

```bat
:: bảng route (hoặc --csv routes.csv); lọc --direction CAN->ETH
python -m ecucstudio gateway routes network.arxml
:: sửa và lưu (mặc định ghi đè file, giữ .bak; -o để ghi file khác). ROUTE = tên PDU Ethernet, frame CAN,
:: triggering hoặc header ID; SOCKET / ENDPOINT = tên ngắn hoặc đường dẫn
python -m ecucstudio gateway edit network.arxml --header EngineData_oBody_Eth=0x1100 ^
    --delete GwCommand_oBody_Eth --port SA_GwEcu_CanGw_Rx=42010 --ip NEP_Tester=10.0.10.9/255.255.255.0
```
`edit` dừng và không ghi gì nếu có lỗi (ví dụ header ID trùng). `--keep-pdus` chỉ xoá mapping.

## 12. Gateway CAN → CAN (nhiều DBC, mỗi DBC một bus)

Thêm mỗi DBC một dòng ở tab **Input**, mỗi dòng chọn **node của ECU gateway trong DBC đó**. Tên node được phép
khác nhau giữa các DBC (ví dụ `Gw_Body` trong DBC Body, `Gw_Chassis` trong DBC Chassis): tool chỉ cần biết node
nào là ECU đang làm trên từng bus.

Có **routing table** của khách hàng (file Excel đi kèm DBC) thì CAN → CAN chỉ lấy theo bảng, gồm cả route signal:
xem mục 17. Phần dưới đây là cách tool tự ghép khi **không** có bảng.

Khi **Analyze**, tool ghép message ECU **nhận** trên một bus với message ECU **gửi** trên bus khác, theo thứ tự:

1. cùng tên message;
2. cùng tên sau khi bỏ tiền tố / hậu tố gateway (`XGW_`, `GW_`, `GTW_`, `GWY_`, `_GW`, `_GTW`), ví dụ
   `BrakeStatus` ↔ `GW_BrakeStatus`;
3. (tuỳ chọn **also pair renamed messages**) cùng CAN ID, cùng loại ID (standard/extended) và cùng độ dài.

Mỗi cặp được kiểm tra trước khi route (PduR gateway chuyển **nguyên PDU**, không đổi signal):

| Trường hợp | Kết quả |
|---|---|
| cùng độ dài, cùng layout signal (start bit, độ dài, byte order) | route, `yes` |
| bus đích chỉ định nghĩa một phần signal của bus nguồn | route, Remark ghi "defines 1 of the 2 signals" |
| độ dài khác nhau | **không** route: "length differs" |
| layout signal khác nhau | **không** route: "signal layout differs (a signal gateway would be needed)" |
| message nhận trên **nhiều** bus (N:1) | **không** route: chọn nguồn bằng **Add CAN -> CAN link…** |
| CAN ID khác nhau (ví dụ `0x200 -> 0x210`) | vẫn route nếu độ dài và layout khớp, cột CAN ID ghi cả hai |

Quan hệ với CAN ↔ Ethernet:

- Message bus đích đã được cấp từ bus CAN khác thì route `ETH->CAN` của nó tự tắt (Remark "fed from Body
  (CAN->CAN)"), để PDU không có hai nguồn. Tắt cặp CAN → CAN thì route `ETH->CAN` tự bật lại.
- Message nguồn vẫn lên Ethernet như cũ (1:N: một PDU CAN, hai đích), Remark ghi "also routed to Ethernet".
- Chỉ cần CAN → CAN: bỏ **CAN <-> Ethernet routes** ở tab Options. Không cần điền tab Ethernet; bảng route chỉ
  còn các dòng `CAN->CAN`.

Trên bảng route, dòng `CAN->CAN` dùng như các dòng khác: Space / chuột phải để bật tắt, double-click xem chi tiết
cặp. **Add CAN -> CAN link…** (chuột phải) ghép tay một message nhận với một message gửi trên bus khác (dùng khi
tên và CAN ID đều khác, hoặc để chọn nguồn cho trường hợp N:1). Link nằm trong `can_links` của cấu hình; dòng
link có thêm mục **Remove CAN -> CAN link**.

Phần tử được tạo cho mỗi cặp: một `I-PDU-MAPPING` trong GATEWAY của ECU, từ PDU-TRIGGERING của bus nguồn tới
PDU-TRIGGERING của bus đích. DaVinci tạo PduR routing path CanIf → CanIf. Sinh lại (mục 7.1) giữ các cặp không
đổi; cặp bị tắt hiện dòng `removed` như route Ethernet.

### 12.1 DBC đã import trong DaVinci: CAN → CAN không lên Com (file `.vsde`)

Khi DBC được import trong project DaVinci (file gateway chỉ chứa phần gateway, mục 2.1 / 2.2), bộ convert DBC của
Vector cho ECU nhận / gửi mọi signal ghi trong DBC. Một message chỉ được route CAN → CAN vẫn lên Com
(CanIf → PduR → Com) và Com vẫn gửi nó (Com → PduR → CanIf). File gateway không xoá được các phần tử đó.

Vì vậy tool ghi các cặp CAN → CAN vào file **`<tên file output>.vsde`** (Vector System Description Extension,
`PDUR-MESSAGE-ROUTING`) cạnh file gateway, và **không** ghi chúng vào file gateway:

1. Thêm file `.vsde` vào **Input Files** của project, cạnh các file DBC (một lần; DaVinci chỉ nhận `.vsde` khi
   project có DBC).
2. Chạy **Update**. Bộ convert DBC đọc file này: message được PduR route CanIf → CanIf, ECU **không** nhận / gửi
   signal nào của nó, nên Com không còn I-PDU của message đó, không còn đường CanIf → Com và Com → CanIf.
3. Sinh lại sau này: tool ghi lại file `.vsde` (bản cũ giữ ở `.vsde.bak`), giữ các cặp đã có (`kept`). Hết cặp
   CAN → CAN thì file vẫn được ghi (rỗng) vì project đang dùng nó.

Message vừa CAN → CAN vừa lên Ethernet (1:N) cũng không lên Com: PduR route tới CanIf và SoAd. Message chỉ đi
CAN → Ethernet giữ nguyên như trước.

Tên ECU trong file `.vsde` là tên ECU của bộ convert: thuộc tính DBC `ECU` của node (ví dụ node `Gw_Chassis` có
`ECU = "Gw"`), không có thì là tên node. Bộ convert bỏ signal của từng bus theo **tên node** trên bus đó, nên khi
tên node khác tên ECU, tool ghi thêm một `PDUR-MESSAGE-ROUTING` cho tên node (cùng các message); GATEWAY chỉ được
tạo một lần (từ routing của tên ECU). Cặp nào không đưa được vào `.vsde` (bus không đến từ DBC, node là hai ECU
khác nhau trong hai DBC, tên không hợp lệ cho bộ convert) thì vẫn nằm trong file gateway và tool báo WARNING (Com
giữ message đó).

Khi thêm file `.vsde` vào Input Files, DaVinci tự ghi loại file là `legacy_communication_modification_script` trong
file `.dpa`.

### 12.2 ETH → CAN: Com không gửi message nhận từ Ethernet

DBC ghi ECU là sender của message, nên Com cũng gửi nó. Khi message đó được route từ Ethernet, CAN PDU có **hai
nguồn** (PduR N:1). DaVinci báo lỗi khi validate: PDUR 13008 "Inconsistent Parameters for same Destination Pdu on
N:1 Routing", COM 02202, COM 02702.

Tool ghi thêm vào cùng file `.vsde` (mục 12.1), với mỗi message ETH → CAN, một `PDUR-MESSAGE-ROUTING` từ PDU tới
chính nó trên cùng bus, `ECU-INSTANCE-REF` là node của ECU trên bus đó. Bộ convert khi đó coi node không gửi signal
nào của PDU, nên Com không còn Tx I-PDU của message; đường SoAd → CanIf của gateway giữ nguyên. Đã kiểm chứng với
DaVinci 5.24: hết lỗi N:1 và các lỗi COM ở trên.

**Lỗi trong log là dự kiến**: khi node có tên trùng tên ECU, bộ convert ghi một lỗi cho mỗi message, ví dụ
`ECU Gw does not receive source pdu BusB.HpcCmd`, và cuối bước convert có dòng "Legacy Converter finished with
errors". Đây là cách dùng định dạng `.vsde` ngoài tài liệu của Vector (định dạng không có nguồn Ethernet). Bộ convert
vẫn ghi kết quả và Update chạy xong (DVCfgCmd trả về 0). Không muốn có các lỗi này thì bỏ chọn **ETH -> CAN: Com does
not send…** ở tab Options; khi đó Com lại gửi các message đó (N:1 như trước).

Sau Update, các CanIf Tx PDU chỉ còn nguồn gateway báo CANIF 10034 (Tx-confirmation), sửa bằng **Solve** như mọi
đích CanIf của gateway (mục 7). Nếu **mọi** message ECU gửi trên một bus đều lấy từ Ethernet / bus khác, Com không
còn Tx PDU nào trên bus đó và DaVinci xoá Com Tx I-PDU group của bus. Các action BswM bật / tắt group đó
(`BswMPduGroupSwitch`, ví dụ `CC_EnablePDUGroup_<ECU>_o<Bus>_Tx`) khi đó báo BSWM 01008 / Cfg 00024 (reference
không có đích). Tool báo WARNING trước cho từng bus như vậy. Xoá các action đó hoặc chạy lại BswM auto configuration.

Ở chế độ project DaVinci (.dpa) tool vẫn tắt route ETH → CAN của message mà Com đang gửi (ECU tự gửi). Bật route đó
bằng tay thì file `.vsde` bỏ Com Tx của nó, không còn cảnh báo N:1.

## 13. Nhiều ECU trên một mạng Ethernet (topology)

Dùng khi có nhiều ECU gateway (ví dụ các zone ECU) cùng nối vào một mạng Ethernet, mỗi ECU có các bus CAN riêng,
và một số message phải đi **thẳng từ ECU này sang ECU kia** qua Ethernet (máy tính trung tâm chỉ làm switch). Ví dụ:
ZoneB nhận `WheelSpeed` trên bus Chassis, ZoneC gửi `WheelSpeed` trên bus Body → ZoneB gửi PDU tới ZoneC.

Mở: menu **Tools → CAN Gateway Topology (several ECUs)…**, `run.bat topology [topology.json]`, hoặc
`python -m ecucstudio gateway topology gui [topology.json]`. Cách nhanh nhất khi chỉ có file DBC: nút **From DBC
files…** (hoặc cửa sổ Start → *I only have DBC files*): tool tìm các ECU từ tên node trong DBC và dựng topology
(xem mục *Bắt đầu nhanh*). Tab **Message paths** của cửa sổ topology hiện đường đi của mọi message (mục 14).

### 13.1 Khai báo mạng

Danh sách **Network** bên trái:

- **(network settings)**:
  - VLAN id của kênh mới (ECU chưa có Ethernet trong base), hoặc **Existing channel** (tên / `VLANnn`) khi base
    của các ECU đã có kênh;
  - netmask, protocol;
  - **One socket per node for both directions** (mặc định bật cho topology mới): mỗi node một socket
    `SA_<node>_CanGw` ở **port của node** (port base trong bảng Ethernet nodes, không có thì port chung, mặc định
    50000); mỗi cặp node một socket connection chở cả hai chiều. Bỏ chọn thì như cũ: **port mọi node gửi** và
    **receive port** (50000 / 50001), một socket gửi và một socket nhận;
  - **Default peer**: node nhận các message không ECU nào cần, và gửi các message không ECU nào cấp (ví dụ máy tính
    trung tâm).
- **Add ECU**: mỗi ECU gateway một mục:
  - tên (dùng trong tên socket `SA_<ECU>_CanGw`, hai socket: `SA_<ECU>_CanGw_Tx` / `_Rx`), IP, port riêng nếu
    khác (IP / port / MAC trống được điền từ bảng Ethernet nodes, mục 3.6);
  - **Network file / project**: project DaVinci `.dpa` (bus = kênh CAN của project), file network, hoặc để trống
    (chỉ có DBC: file mới, như mục 2.1). Các ECU trộn được nhiều kiểu;
  - **Output file** và **Generate**. ECU bỏ **Generate** chỉ để tham chiếu: tool vẫn đọc DBC / project của nó để biết
    nó cần và gửi message nào, nhưng không ghi file cho nó (zone do team khác làm). Không cần output file;
  - **CAN buses**: mỗi DBC (chọn node của ECU trong DBC đó) hoặc kênh của project một dòng.
- **Add peer**: node chỉ có Ethernet (tên, IP). Không sinh file cho peer.
- **Up / Down**: thứ tự ECU quyết định thứ tự cấp header ID tự động.

### 13.2 Analyze: tool ghép message giữa các ECU

Với mỗi message một ECU **gửi** trên bus của nó, tool tìm nguồn:

1. bus khác của chính ECU đó → CAN → CAN nội bộ (mục 12, ưu tiên);
2. bus của ECU khác mà ECU kia **nhận** message đó → đường **ECU → ECU** qua Ethernet;
3. không có → nhận từ default peer (bỏ chọn *comes from the default peer* thì không route).

Quy tắc ghép và kiểm tra giống CAN → CAN (cùng tên / bỏ tiền tố GW / cùng CAN ID + độ dài; khác độ dài hoặc layout
thì không route). Message nhiều ECU cùng nhận (N:1) không được route: chọn nguồn bằng **Add link…**.

- Message ECU → ECU **không** gửi default peer nữa (bật *also sent to the default peer*, hoặc từng route trong
  Edit…). Các message còn lại ECU nhận vẫn gửi default peer như trước.
- Hai đầu dùng chung **tên PDU Ethernet** (theo bên gửi, ví dụ `WheelSpeed_oChassis_Eth`), **header ID**, IP và
  port: một socket thì file của ZoneB có connection `SA_ZoneB_CanGw ↔ SA_ZoneC_CanGw` và file của ZoneC có
  `SA_ZoneC_CanGw ↔ SA_ZoneB_CanGw` (hai socket: `SA_ZoneB_CanGw_Tx → SA_ZoneC_CanGw_Rx`), cùng identifier.
- Header ID được cấp **một lần cho cả mạng**: duy nhất trên socket gửi và trên **mọi socket nhận** (socket nhận của
  default peer nhận từ mọi ECU, nên CAN ID trùng giữa hai zone sẽ được thêm cờ), tính cả ID base các ECU đã dùng.
  Thứ tự: nhập tay → file lock → file sinh lần trước → tự động.

Bảng bên dưới:

| Tab | Nội dung |
|---|---|
| ECU -> ECU | mọi cặp tìm được: bật/tắt (Space, chuột phải), **Edit…** (header ID chung, gửi thêm default peer), **Add link…** / **Remove link** |
| Routes of the selected ECU | bảng route của ECU đang chọn (như generator một ECU, có cột Peer); chuột phải tắt/bật message |
| Contract (Ethernet PDUs) | mọi PDU Ethernet của mạng: bên gửi, bên nhận, IP:port hai đầu, header ID |

### 13.3 Generate

**Generate** ghi (topology phải được Save trước):

- file gateway của mọi ECU có **Generate**, đúng định dạng của mục 6 / 2.2 (project → file bổ sung cho Input Files);
- `<topology>.lock.json`: header ID của mọi PDU Ethernet. **Không sửa tay; luôn để cùng file topology** (ví dụ cùng
  repo). Team khác sinh ECU của họ sau, từ cùng topology + lock, sẽ được đúng giá trị file của bạn đang dùng;
- `<topology>_contract.csv`: tài liệu giao diện (cho node không dùng tool, ví dụ máy tính trung tâm);
- `<topology>_message_paths.html` / `.csv`: đường đi của mọi message qua các ECU (mục 14).

Sinh lại: file gateway cũ của mỗi ECU tự được dùng làm file "previous" (mục 7.1), DaVinci giữ nguyên phần không đổi.

### 13.4 Command line và file cấu hình

```
python -m ecucstudio gateway topology plan     topology.json
python -m ecucstudio gateway topology generate topology.json [--ecu ZoneB --ecu ZoneC]
python -m ecucstudio gateway topology contract topology.json [-o contract.csv]
```

```json
{
  "name": "Zonal",
  "ethernet": {"channel": "", "vlan_id": 60, "netmask": "255.255.255.0", "protocol": "UDP"},
  "tx_port": 50000, "rx_port": 50001,
  "peers": [{"name": "Central", "ip": "10.0.60.1"}],
  "default_peer": "Central",
  "ecus": [
    {"name": "ZoneA", "ip": "10.0.60.11", "generate": false,
     "gateway": {"base": "ZoneA/ZoneA.dpa", "buses": [{"channel": "Sensor"}]}},
    {"name": "ZoneB", "ip": "10.0.60.12", "generate": true,
     "gateway": {"base": "ZoneB/ZoneB.dpa", "output": "ZoneB/ZoneB_Gateway.arxml",
                 "buses": [{"channel": "Power"}, {"channel": "Chassis"}]}},
    {"name": "ZoneC", "ip": "10.0.60.13", "generate": true,
     "gateway": {"base": "", "output": "ZoneC/ZoneC_Gateway.arxml", "ecu": "ZoneC",
                 "buses": [{"dbc": "DBC/Body.dbc", "node": "ZoneC"}]}}
  ],
  "cross": {"also_to_default_peer": false, "from_default_peer": true, "match_id": true},
  "routes": {"ZoneB/Chassis/WheelSpeed -> ZoneC/Body/WheelSpeed": {"header_id": "0x120"}},
  "links": [{"src": "ZoneB/Chassis/VehSpeed", "dst": "ZoneC/Body/VehSpeed"}]
}
```

- `ecus[].gateway` là cấu hình gateway một ECU (mục 9); phần Ethernet (IP, socket, peer) do topology điền.
- `ecus[].sockets` (không bắt buộc): dùng socket có sẵn trong base cho một đối tác:
  `{"Central": {"can_to_eth": {"local_socket": "...", "remote_socket": "..."}, "eth_to_can": {...}}}`.
- `routes` / `links`: khoá như cột From / To của bảng ECU -> ECU (`<ECU>/<bus>/<message>`).
- Kiểm tra trước khi sinh: tên node trùng hoặc có ký tự lạ, IP trùng, IP của ECU khác IP trong project, port của
  hai đầu lệch, header ID nhập tay trùng.

## 14. Report đường đi message

File report cho biết **mỗi message xuất phát từ đâu, đi qua những ECU gateway nào và đi về đâu**.

- Một message được nhận diện bằng **CAN ID** (và loại ID standard / extended): cùng CAN ID trên nhiều kênh là
  **một** message (cột Message ghi các tên của nó, ví dụ `PowerState / RadarObj`). Cặp CAN → CAN / ECU → ECU giữa
  message đổi ID (ghép bằng link) cũng gộp vào cùng một dòng chảy.
- Mỗi dòng là một đường đi:

  | Cột | Nội dung |
  |---|---|
  | From | node gửi message trên bus của nó (`Abs @ Chassis`), hoặc node Ethernet (`Central (Ethernet)`). Với project DaVinci, file Communication chỉ biết ECU của project nên chỉ ghi `bus Chassis` |
  | Via | các ECU gateway theo thứ tự, chặng Ethernet ghi header ID: `ZoneB → [ETH 0x00000120] → ZoneC`; đi qua một bus giữa hai gateway ghi `bus X` |
  | To | bus đích và các node nhận (`Body → Door`), hoặc node Ethernet |
  | Gateways | danh sách ECU gateway trên đường đi |
  | Header ID | header ID của các chặng Ethernet trên đường đi (trống nếu chỉ đi CAN) |
  | ETH send | khi bật gom PDU (mục 16): chặng CAN → ETH được gom (`collect <= 5 ms`) hay gửi ngay (`immediate`) |

- Message đi tới nhiều nơi (1:N) có nhiều dòng. Node Ethernet là điểm cuối / điểm đầu: tool không biết node đó có
  chuyển tiếp message hay không, nên không nối hai chiều qua nó.
- Bảng **Not routed**: message có thể route nhưng không route, kèm lý do (bị tắt, khác layout, N:1 …).
- Khi bật gom PDU (mục 16) có thêm hai bảng: **Ethernet load** (số gói/s và Mbit/s gửi 1:1 so với khi gom, cho
  từng node Ethernet) và **ETH -> CAN bursts** (số frame CAN dồn cùng lúc trên mỗi bus nếu node Ethernet cũng gom,
  thời gian chiếm bus).

Cách tạo:

| Ở đâu | File |
|---|---|
| Generator: **Generate** (tự ghi), hoặc nút **Message Report** (ghi và mở) | `<output>_message_paths.html` + `.csv` cạnh file output |
| Topology: **Generate** (tự ghi), hoặc nút **Message Report** | `<topology>_message_paths.html` + `.csv` cạnh file topology: đường đi qua **mọi** ECU của mạng |
| Command line | `python -m ecucstudio gateway report gateway.json` (hoặc file gateway `.arxml`, hoặc `topology.json`) `[-o tên]` |

File HTML mở bằng trình duyệt, có ô lọc (CAN ID, tên message, bus, ECU). File CSV (dấu `;`) mở bằng Excel.

## 15. CAPL test cho đường ETH → CAN (CANoe)

Nút **CAPL Test (ETH->CAN)** trên thanh công cụ (hoặc `python -m ecucstudio gateway capl <gateway.json | file
gateway .arxml>`) sinh file `<tên file output>_eth_to_can_<node>.can`, mỗi node Ethernet nguồn của các route
ETH → CAN một file (thường là node mặc định, ví dụ máy tính trung tâm).

- Node CAPL đóng vai node Ethernet đó: mở UDP socket tại IP / port của node (theo file gateway), gửi tới IP / port
  ETH → CAN của ECU gateway.
- Mỗi PDU Ethernet là một UDP datagram: SoAd PDU header (header ID 4 byte + length 4 byte, big endian) + payload.
  Payload là giá trị khởi tạo của signal trong DBC (`GenSigStartValue`, đúng start bit / byte order).
- PDU 1:N (mục 3.5) chỉ gửi một lần; đầu file liệt kê các frame CAN mà gateway phải gửi ra cho từng PDU.

Trong CANoe:

1. Thêm file `.can` làm simulation node trên mạng Ethernet. TCP/IP stack của node: IP của node nó đóng vai (ghi ở
   đầu file), VLAN như kênh Ethernet.
2. Chạy measurement, mở Trace của các kênh CAN của ECU gateway.
3. Phím: `a` gửi tất cả một lần, `n` gửi PDU tiếp theo (ghi tên + frame mong đợi ra Write window), `c` bật / tắt gửi
   theo chu kỳ (cycle time trong DBC, không có thì 100 ms), `p` đổi payload sang bộ đếm (mỗi byte = giá trị đếm, để
   thấy dữ liệu thay đổi trên CAN), `l` liệt kê PDU.
4. Thử nhận gói gom (mục 16): `b` gửi mọi PDU trong ít UDP datagram nhất (mỗi gói tối đa bằng buffer gom, không có
   thì 1472 byte), `v` bật / tắt gửi theo chu kỳ kiểu gom: mỗi `timeout` ms các PDU đến hạn đi chung một datagram.
   Trên Trace CAN phải thấy đủ các frame, dồn cục cùng lúc.

IP / port không xác định được từ file gateway thì để `0.0.0.0` / `0` kèm dòng `// !!` ở đầu file: sửa trong khối
`variables`. Socket TCP chưa hỗ trợ (script gửi UDP).

## 16. Gom nhiều PDU trong một gói UDP (PDU collection, SoAd nPdu)

Mặc định mỗi frame CAN nhận được là một UDP datagram gửi ngay (1:1). Bật gom thì SoAd chép PDU vào buffer của
socket connection và gửi chung một datagram khi hết thời gian chờ. Mỗi PDU vẫn có header ID riêng, frame CAN nào
đến cũng được gửi đúng một lần, không có frame mới thì không gửi gì.

```
UDP payload: [header ID 1][len 1][data 1][header ID 2][len 2][data 2] ...   (thứ tự theo lúc nhận)
```

Tab **Ethernet**, khung **PDU collection**:

| Ô | Ý nghĩa | Mặc định |
|---|---|---|
| Collect | bật gom (chiều CAN → ETH) | tắt |
| timeout ms | PDU được gom chờ tối đa bao lâu. SoAd kiểm tra trong main function nên dùng chu kỳ `SoAdMainFunctionPeriod` hoặc bội của nó | 5 |
| buffer bytes | kích thước tối đa một datagram (gồm header 8 byte mỗi PDU). Đầy thì gửi rồi bắt đầu buffer mới; ≤ 1472 để không bị phân mảnh IP | 1400 |
| collect every PDU / send at once … ≤ X ms | gom tất cả, hoặc gửi ngay message event và message có chu kỳ ≤ X ms (còn lại gom) | gom tất cả |

Từng message: bảng route, chuột phải → **ETH send (PDU collection)** → Collect / Send immediately / Automatic.
Cột **ETH send** của bảng route ghi kết quả (`collect <= 5 ms`, `immediate (cycle 10 ms <= 20 ms)`, …).

File gateway được ghi thêm:

| Thuộc tính | Ở đâu | DaVinci sinh |
|---|---|---|
| `PDU-COLLECTION-MAX-BUFFER-SIZE` | socket local CAN → ETH | `SoAdSocketnPduUdpTxBufferMin` |
| `PDU-COLLECTION-TIMEOUT` | socket local CAN → ETH | `SoAdSocketUdpTriggerTimeout` |
| `PDU-COLLECTION-TRIGGER` = `NEVER` / `ALWAYS` | `SO-CON-I-PDU-IDENTIFIER` của route CAN → ETH | `SoAdTxUdpTriggerMode` = `TRIGGER_NEVER` / `TRIGGER_ALWAYS` |
| `PDU-COLLECTION-PDU-TIMEOUT` | identifier của PDU được gom | `SoAdTxUdpTriggerTimeout` |
| `PDU-COLLECTION-SEMANTICS` = `QUEUED` | identifier | `SoAdTxIfTriggerTransmit = false` |

`QUEUED`: SoAd chép data lúc nhận, mọi frame đều vào gói (cùng CAN ID đến 2 lần trong một cửa sổ thì có cả 2). Tool
không dùng `LAST-IS-BEST`: SoAd khi đó lấy data qua TriggerTransmit lúc gửi, mỗi PDU chỉ một lần (frame trước bị
bỏ, E2E counter nhảy), PduR phải có data provision TRIGGER_TRANSMIT + Single Buffer cấu hình tay, DaVinci báo
SOAD 01701. Đã kiểm chứng với DaVinci 5.24 và mã nguồn SoAd.

Chiều ETH → CAN không cần cấu hình: SoAd tự tách datagram có nhiều PDU. Nhưng nếu node Ethernet (máy tính trung
tâm) cũng gom, các PDU của một datagram ra CAN cùng lúc: tool ước lượng số frame dồn cục và thời gian chiếm bus
(baudrate trong DBC, không có thì 500 kbit/s) và báo WARNING khi quá 50 % cửa sổ. Cần buffer Tx của CanIf đủ lớn.

Analyze báo ước lượng tải cho từng node Ethernet (`PDU collection to Central: 48 collected, 1 immediate …: 1:1 ~ 1665
pkt/s … -> collected ~ 300 pkt/s …`); report đường đi có bảng **Ethernet load** và **ETH -> CAN bursts** (mục 14).

Lưu ý:

- Socket local có sẵn trong project (tool không tạo): file bổ sung không sửa được nó. Tool báo WARNING; đặt
  `SoAdSocketnPduUdpTxBufferMin` / `SoAdSocketUdpTriggerTimeout` trong DaVinci. Trigger của từng PDU vẫn nằm trong
  file.
- Phía nhận (máy tính trung tâm) phải đọc được nhiều PDU trong một datagram.
- Mạng nhiều ECU: cài đặt gom nằm trong cấu hình gateway của từng ECU (`ethernet.collection`).

## 17. Routing table của khách hàng (CAN → CAN theo bảng)

Khách hàng gửi kèm DBC một file Excel liệt kê từng message / signal cần route và route từ mạng nào sang mạng nào.
Chọn file ở tab **Input**, khung **Routing table (CAN -> CAN)** (wizard: trang *DBC files of the network*; topology:
*Network settings* → *Routing table*). Khi có bảng:

- **CAN → CAN chỉ lấy theo bảng**: tool không tự ghép message theo tên / CAN ID (mục 12), link CAN → CAN không dùng;
- dòng **message** thành route PduR (nguyên PDU), dòng **signal** thành Com signal gateway;
- CAN ↔ Ethernet vẫn theo DBC như cũ.

Không chọn bảng thì mọi thứ như mục 12.

### 17.1 Định dạng

`.xlsx` (sheet đầu tiên có dòng tiêu đề; dòng tiêu đề có thể nằm dưới vài dòng tiêu đề khác) hoặc `.csv` / `.tsv`
(dấu `;`, `,` hoặc tab). Cột được nhận theo **tên tiêu đề**, không theo thứ tự:

| Cột | Dùng cho |
|---|---|
| cột đầu (không tên) | số thứ tự của bảng, hiện trong report: `row 12 #10` = dòng 12 của sheet, số 10 của bảng |
| Signal name | signal nguồn (dòng signal) |
| Src Message/PDU name | message nguồn |
| Receive CAN … PduID (Hex) | CAN ID nguồn (hex), dùng khi tên message không có trong DBC |
| Routing Type (Signal = 1, Message = 0) | 0 = route nguyên message (PduR), 1 = route signal (Com) |
| HW-Accelerator (LLCE/PFE) (Yes = 1, No = 0) | 1 = LLCE / PFE route bằng phần cứng (mục 17.3) |
| các **cột mạng** (giữa HW-Accelerator và Dest Signal name) | `S` = mạng nguồn (đúng một), `D` = mạng đích (một hoặc nhiều) |
| Dest Signal name / Dest Message/PDU name | signal / message đích (trống = giống nguồn) |
| Transmit CAN … PduID (Hex) | CAN ID đích (hex) |

Các cột khác (Length, Timeout, Default / Invalid value, KeepAlive …) được đọc nhưng chưa dùng.

**Cột mạng ↔ bus**: tool khớp tên cột với tên bus, DBName hoặc tên file DBC (không phân biệt hoa thường, bỏ qua
`_` / `-`), hoặc với một từ của tên file (cột `BusA` ↔ `Vehicle_BusA_v3.dbc`). Không khớp thì chọn ở **Edit…** của
bus → **Routing table network** (ô bên cạnh ghi `empty = <cột tự khớp>`). Analyze in `Routing table networks:
BusA = BusA, …` và các bus không có cột.

### 17.2 Dòng message, dòng signal

- **Message** (Routing Type 0): mỗi mạng đích `D` là một route CAN → CAN như mục 12 (PduR, nguyên PDU). Theo DBC,
  ECU phải **nhận** message trên bus `S` và **gửi** message đích trên bus `D`. Độ dài / layout signal được kiểm
  tra như mục 12 (khác thì route tắt, bật tay được). DBC đã import trong DaVinci: ghi vào `.vsde` (mục 12.1).
- **Signal** (Routing Type 1): Com signal gateway. Theo DBC, ECU phải nhận signal nguồn (node có trong receiver của
  signal) và gửi message chứa signal đích. Tool ghi vào `.vsde` một `COM-SIGNAL-ROUTING`; bộ convert DBC của
  DaVinci tạo `I-SIGNAL-MAPPING` trong GATEWAY của ECU, DaVinci tạo một `ComGwMapping` cho mỗi signal. Chỉ dùng
  được khi DBC được import trong DaVinci (file gateway-only, mục 2.1 / 2.2).
- Message đích của route message / signal không lấy từ Ethernet nữa: route `ETH->CAN` của nó tắt ("fed from …" /
  "Com sends it (signals routed from …)").
- Message nguồn vừa được route nguyên PDU vừa có signal được route: tool ghi `SOURCE-SIGNALS` vào `.vsde` để Com
  vẫn nhận các signal đó (Remark "Com still receives …"). Không có thì converter bỏ signal khỏi Com và signal
  routing hỏng (đã thử với converter).

Trên bảng route, dòng signal có Direction `SIGNAL`, cột Message `MsgA.SigA -> MsgB.SigB`, cột Length là số bit;
bật / tắt (Space, chuột phải) và double-click như dòng `CAN->CAN` (lưu trong `can_gateway` của cấu hình).

Xung đột (route tắt, Remark ghi lý do):

| Trường hợp | Kết quả |
|---|---|
| hai dòng cấp cùng một message đích bằng PduR (N:1) | dòng sau tắt: "one source per PDU" |
| signal đi vào message đích đã được route nguyên PDU | route signal tắt |
| hai dòng ghi vào cùng một signal đích | dòng sau tắt |
| độ dài signal nguồn và đích khác nhau | vẫn route, WARNING |
| DBC không ghi ECU là receiver của signal nguồn | route signal tắt (converter sẽ bỏ qua nó) |

Lần đầu project có signal routing, validation của DaVinci báo (đã kiểm chứng với DaVinci 5.24, `ComGwMapping` được
tạo đúng):

| Validation | Làm gì (một lần) |
|---|---|
| COM01009 `ComSignalGateway` = NONE | đặt `/Com/ComGeneral/ComSignalGateway` = `COMPLETESIGNALPROCESSING` (hoặc `MINIMALSIGNALPROCESSING`) |
| COM02702, RTE01216 partition | project nhiều partition: `ComMainFunctionRouteSignals` và các PDU mới của Com cần partition ref (`ComMainRouteSignalsPartitionRef`, `EcucPduDefaultPartitionRef`) như các phần tử khác |
| COM02600, PDUR13200 | message đích nay do Com gửi, PDU nguồn có thêm đích Com: Solve như thường lệ |
| COM02325 `ComSignalAccess` (warning) | Solve |

Kiểm chứng end-to-end: file gateway + `.vsde` do tool sinh từ một routing table (1 dòng message, 3 dòng signal, một
signal lấy từ chính message được route nguyên PDU) → DaVinci tạo 3 `ComGwMapping`; routing path của message đó có ba
đích CanIf + SoAd + Com, Com chỉ nhận signal được route.

### 17.3 Dòng không được route

| Trạng thái | Nghĩa |
|---|---|
| HW accelerator | HW-Accelerator = 1: LLCE / PFE route bằng phần cứng. Tool không tạo route PduR / Com và tắt route `ETH->CAN` của message đích (không có nguồn thứ hai). Muốn route bằng phần mềm: tick **Route HW accelerator rows too** |
| LIN | mạng nguồn hoặc đích là LIN (chưa hỗ trợ) |
| Ethernet | mạng đích là cột Ethernet: CAN ↔ Ethernet vẫn theo DBC |
| other ECU / not this ECU | bus của ECU khác (đi qua Ethernet, theo DBC) / không phải bus của ECU đang sinh |
| problem | dòng sai (không có `S`, Routing Type lạ, ID không phải hex …) hoặc không khớp DBC (không có message / signal, ECU không nhận / không gửi) |
| off | route được tạo nhưng tắt (xung đột, khác độ dài / layout, người dùng tắt) |

Analyze in tổng kết `Routing table <file>: N row(s); for <ECU>: x message route(s), y signal route(s), …`. Report
đường đi (mục 14) có thêm bảng **Routing table**: mỗi dòng của bảng, gateway, trạng thái và chi tiết từng mạng
đích (ô lọc của HTML lọc cả bảng này); file CSV có cùng nội dung.

Mạng nhiều ECU (topology): một bảng cho cả mạng (`routing_table` trong file mạng, đường dẫn tương đối so với file
mạng); mỗi ECU dùng các dòng có bus của nó. Cấu hình của một ECU có `routing_table` riêng thì dùng bảng đó.

Cấu hình (`gateway.json`): `routing_table`, `options.table_hw`, `buses[].table_network`.

## Giới hạn hiện tại

- Route nguyên PDU. PDU CAN đã có route trong base thì được thêm đích Ethernet (thành 1:N). CAN → CAN có 1:N
  (một nguồn, nhiều bus đích) nhưng không có N:1; khác layout signal thì dùng dòng signal của routing table
  (mục 17, chỉ khi DBC được import trong DaVinci). Routing table: dòng LIN và Ethernet chưa được route.
- Message multiplexed và PDU không phải I-SIGNAL-I-PDU (ví dụ SecOC) được route nguyên PDU, không có signal.
- TCP: tạo TCP-TP-PORT và TCP-ROLE, các tham số TCP khác dùng mặc định của DaVinci.
