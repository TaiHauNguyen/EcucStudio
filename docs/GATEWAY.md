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

## Bắt đầu nhanh: cửa sổ **Start**

Mở tool (`run.bat gateway` hoặc menu **Tools**) là cửa sổ **Start** hiện ra (mở lại bằng nút **Start…**).

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
2. **ECUs, IP addresses and network file**: IP của từng ECU (gợi ý sẵn, theo VLAN hoặc theo IP có trong project),
   VLAN, port gửi / nhận chung, node trung tâm (tuỳ chọn, mặc định không có). Tất cả lưu trong **file mạng**
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

- Python 3.10+ cùng các gói `lxml` và `cantools` (`py -m pip install -r requirements.txt`).
  `run.bat` tự kiểm tra và tự cài nếu thiếu.
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
| new socket name / port | khi tạo mới: tên (bỏ trống = `SA_<ECU>_CanGw_Tx` / `_Rx`) và **port của ECU** |
| Remote socket | socket có sẵn của node bên kia, hoặc `<create new>` |
| remote IP / port | khi tạo mới: **IP và port của node bên kia** (IP đã có trong VLAN thì dùng lại endpoint đó) |
| remote socket name | tên socket remote mới (bỏ trống = tên mặc định) |
| Socket connection name | tên STATIC-SOCKET-CONNECTION mới (bỏ trống = dùng connection có sẵn tới remote đó, hoặc `<local>_to_<remote>`) |

Hai chiều có thể dùng chung một socket.

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

## 4. Tab **Options & Naming** (không bắt buộc)

- **CAN <-> Ethernet routes**: tạo route CAN ↔ Ethernet (mặc định bật). Tắt đi khi chỉ cần CAN → CAN: không cần
  điền tab Ethernet và file không có phần Ethernet nào.
- **CAN -> CAN routes**: ghép message giữa các bus (mục 12, mặc định bật).
  - **also pair renamed messages**: ghép cả message bị đổi tên nhưng cùng CAN ID và độ dài (mặc định bật).
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
  - **port mọi node gửi** và **port mọi node nhận** (mặc định 50000 / 50001). Mỗi node có một socket gửi và một
    socket nhận, mỗi đối tác là một socket connection;
  - **Default peer**: node nhận các message không ECU nào cần, và gửi các message không ECU nào cấp (ví dụ máy tính
    trung tâm).
- **Add ECU**: mỗi ECU gateway một mục:
  - tên (dùng trong tên socket `SA_<ECU>_CanGw_Tx` / `_Rx`), IP, port riêng nếu khác;
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
  port: file của ZoneB có connection `SA_ZoneB_CanGw_Tx → SA_ZoneC_CanGw_Rx`, file của ZoneC có connection
  `SA_ZoneC_CanGw_Rx ← SA_ZoneB_CanGw_Tx`, cùng identifier.
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

- Message đi tới nhiều nơi (1:N) có nhiều dòng. Node Ethernet là điểm cuối / điểm đầu: tool không biết node đó có
  chuyển tiếp message hay không, nên không nối hai chiều qua nó.
- Bảng **Not routed**: message có thể route nhưng không route, kèm lý do (bị tắt, khác layout, N:1 …).

Cách tạo:

| Ở đâu | File |
|---|---|
| Generator: **Generate** (tự ghi), hoặc nút **Message Report** (ghi và mở) | `<output>_message_paths.html` + `.csv` cạnh file output |
| Topology: **Generate** (tự ghi), hoặc nút **Message Report** | `<topology>_message_paths.html` + `.csv` cạnh file topology: đường đi qua **mọi** ECU của mạng |
| Command line | `python -m ecucstudio gateway report gateway.json` (hoặc file gateway `.arxml`, hoặc `topology.json`) `[-o tên]` |

File HTML mở bằng trình duyệt, có ô lọc (CAN ID, tên message, bus, ECU). File CSV (dấu `;`) mở bằng Excel.

## Giới hạn hiện tại

- Route nguyên PDU. PDU CAN đã có route trong base thì được thêm đích Ethernet (thành 1:N). CAN → CAN có 1:N
  (một nguồn, nhiều bus đích) nhưng không có N:1; khác layout signal cần signal gateway, tool không tạo.
- Message multiplexed và PDU không phải I-SIGNAL-I-PDU (ví dụ SecOC) được route nguyên PDU, không có signal.
- TCP: tạo TCP-TP-PORT và TCP-ROLE, các tham số TCP khác dùng mặc định của DaVinci.
