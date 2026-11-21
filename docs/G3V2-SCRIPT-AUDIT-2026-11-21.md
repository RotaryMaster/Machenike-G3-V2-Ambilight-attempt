# G3V2 — Auditoria estática de scripts recebidos (21/11/2026)

Arquivos recebidos diretamente do usuário e analisados **sem executá-los**. O código-fonte original não foi automaticamente publicado no GitHub; esta auditoria registra SHA256, comportamento e restrições, sem dados pessoais.

## g3v2-light-write-timing-v2.py

- SHA256 original: `1785cad636599330d417300a7a977e64f63a1ce1e1f5c76708d06f0b30f3568b`.
- Código Python recebido: 296 linhas. `--dry-run` é `store_true` no parser (linha ~168).
- Toda a sequência de escrita (`save_snapshot`, `do_write`, warmup escrito, laços de 8+8) encontra-se **dentro de `if not args.dry_run:`**, assim como as estatísticas de timing de escrita.
- `p=load_persist()` importa `tools/g3v2-light-0x37-persistence-test.py`, que por sua vez importa `g3v2-warmup-readonly.py`. Os dois módulos possuem `if __name__ == "__main__"` para proteger a entrada principal; **não há rotinas de escrita executadas no momento da importação** nas versões conferidas do repositório.
- `p.warmup_and_capture(fd)` usa identidade e requisições KeyLinker **somente de leitura** (`B0/B1/B2/B3/B4/B7/BB`). O dispositivo HID é aberto O_RDWR porque o transporte envia pacotes de consulta, **não** porque o `--dry-run` grava a iluminação.
- Com `--dry-run`, o corpo de `do_write` e seu `send_outer(..., 0x37)` não são alcançáveis pelo fluxo normal. A suspeita anterior de `dry-run` defeituoso, levantada ao ler uma transcrição com comando/log não pareados, **não se confirmou no código**.
- A execução real por padrão usa 17 transmissões `0x37` (1 igual descartada + 8 iguais + 8 alterando valor); o teto é 24. `choose_marker` troca RGB e preserva os demais bytes. O B7 final deve corresponder ao original antes de remover o snapshot.
- **Limitação:** a função `timed_write` cronometra a primeira resposta externa, mas `do_write` verifica somente se ela não é `None`; o caminho não valida todas as semânticas de `ACK`/`flags`. O B7 posterior valida os seis bytes de configuração, **não o modo visual real**, que pode permanecer alterado.
- **Limitação:** após erro/interrupção pode restar estado modificado e um snapshot de restauração. `--restore` é ele próprio uma nova escrita `0x37`, que não deve ser feita sem necessidade real.
- **Conclusão:** dry-run corretamente protegido contra escritor 0x37 no código auditado; teste completo não é indicado para ser repetido, e não comprova RAM/Flash/wear safety.

## g3v2-le-connect-diag-v1.py

- SHA256 original: `7859fe28692a000041e08da9ed6021cebcb78c37840acfb1323a006b66a5dd35`.
- Código Python recebido: 165 linhas.
- Detecta MAC G3V2 via `bluetoothctl devices`; se ausente, tenta `bluetoothctl --timeout 12 scan on`. Há varredura Bluetooth ativa como operação de descoberta, **não** configuração de iluminação.
- Tenta até três variantes de `AF_BLUETOOTH`, `SOCK_SEQPACKET`, `BTPROTO_L2CAP`, `CID 4`, com `LE_PUBLIC` (timeout ou não bloqueante) e, em último caso, `LE_RANDOM`; cada socket é fechado no `finally`.
- Não chama `send`, `sendall`, `write` ou operações ATT/GATT/KeyLinker; não altera CCCD, nem usa pareamento/OTA/SCSI.
- Mascara o endereço nas saídas (`XX:XX:XX`), mas os próprios argumentos de terminal, quando usados com MAC literal, podem permanecer no histórico do shell; evite divulgá-los publicamente.
- Conexão LE já havia sido demonstrada anteriormente (v1) e a leitura BLE Android `0x91`/`B7` funciona. **Não repetir o diagnóstico** como objetivo científico.

## Diretriz operacional

Ambos os arquivos foram apenas **inspecionados** neste trabalho. Não houve acesso ao controle, atualização de firmware, gravação RGB ou alteração na base imutável do Bazzite. A documentação técnica deve tratar a existência de uma transcrição contendo `--dry-run` junto de TXs como **ambiguidade de colagem**, não bug comprovado. Não publicar o script v2 de escrita no diretório executável de ferramentas sem decisão explícita; já não há necessidade de medições repetitivas. O problema de desgaste do `0x37` permanece não resolvido.

## Testes executados OFFLINE em 21/11/2026 — simulação de fluxo

A inspeção estática foi complementada com uma execução **inteiramente simulada** dos próprios caminhos de função dos dois arquivos Python recebidos. A execução isola o código fornecido de seus módulos de dispositivo, substitui operações HID/BLE por objetos falsos e utiliza apenas buffers RGB em memória (sem tocar em `/dev/hidraw`, adaptador Bluetooth ou `G3V2`).

Resultados:
- `python3 -m py_compile`: **PASS** nos dois arquivos fornecidos.
- `g3v2-light-write-timing-v2.py --dry-run --reads 3`, com `p/g` falsos: **PASS**, 6 leituras B7 simuladas e **0 escritas 0x37**; nenhum snapshot salvo.
- Caminho completo padrão da mesma ferramenta, novamente com `p/g` falsos: **PASS**, **17 comandos 0x37 simulados**, 24 leituras B7 simuladas, 1 snapshot fictício, verificação final `B7 == original`.
- `g3v2-le-connect-diag-v1.py --address` com `socket` falso: **PASS**, 1 conexão simulada, socket fechado, **nenhuma operação de envio de dados**; MAC fictício mascarado na saída.
- Esses são testes de isolamento/regressão de software, **não** testes reais de resposta do dispositivo, wear-leveling, integridade de NVM, estado visual ou resultado de flashing.

**Conclusão atualizada:** a dúvida sobre o `--dry-run` fica **resolvida para o código fornecido**. O log anterior da conversa exportada continha uma saída de execução com escritas sem demonstração de pertencer ao mesmo comando `--dry-run`. A hipótese de bug no `--dry-run` não foi reproduzida.

A execução completa real do script continua **desaconselhada**: não demonstra desgaste seguro e não é necessário repetir as gravações já feitas. O arquivo da bateria de testes local, se compartilhado, é auxiliar e não deve executar o G3V2.
