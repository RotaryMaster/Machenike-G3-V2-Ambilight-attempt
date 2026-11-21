# G3V2 / ZXD2267 — roteiro para recuperar firmware legível do FOT

Status: investigação de artefatos e criptografia **offline**, 2026-11-21.

## Objetivo e limites

Recuperar o programa executável que controla o MCU e os efeitos RGB
a partir de imagens de atualização oficiais `.fot`, **sem** entrar
no bootloader, enviar SCSI, executar EXE, gravar 0x37/0x3B,
ou modificar o controlador.

Separar três resultados diferentes:
1. obter **outra versão criptografada** do mesmo HWID;
2. demonstrar características da transformação ou recuperar trechos
   conhecidos de plaintext (análise diferencial);
3. recuperar o firmware completo e validar arquitetura/código.
Um não implica automaticamente o seguinte.

## O que já está fechado

- O G3V2 físico tem firmware v1.48 e identidade KeyLinker 1006:2011.
- HWID da imagem oficial: `a1ca51da724cb6103658c05d7d4e2267`
  (família ZXD2267).
- Atualizador v1.48 oficial:
  SHA256 `f0cf653ae3d3de70266b38d011ba6491a3d454aed5cf0a6ac482cbbfaa50c255`.
- FOT v1.48 local A: SHA256
  `0b97b5e38fc706182110cdfa1682b1c252a1c0dac8314f5c836d3f037a67e836`.
- FOT v1.48 oficial B: SHA256
  `0ad5d080c6451d9a26a28e32f77ce73789f163ce8cad12c035ff4314b10fb085`.
- Embora os dois FOT inteiros tenham hashes diferentes,
  `XCOD` (361472 bytes) e `XRES` (3072 bytes) são
  **100% idênticos**; mudanças apenas no header e sua cópia,
  0x101 e 0x115..0x117, relacionadas à identidade BT.
  **Não repetir tentativa de obter plaintext via XOR desses dois FOTs.**
- Os registros de seções usam tags obfuscadas com XOR 0x80;
  isso **não** descriptografa os bytes de XCOD/XRES.
- O updater transfere bytes do FOT via 722 blocos F0;
  não contém rotina de descriptografia de payload no host.
- Tentativas anteriores de métodos simples / estatísticas
  de entropia não forneceram uma imagem de CPU desassemblável.
- Análise local de ~19 mil arquivos já encontrou duas imagens v1.48
  com código idêntico. Não refazer esse scan sem novos diretórios
  ou novos arquivos.

## Pesquisa pública verificada

**LopeKinz/Manba-One-V2-Firmware-tools** investigou a mesma família
FOT em ZXD2055 e firmwares relacionados:
`https://github.com/LopeKinz/Manba-One-V2-Firmware-tools`.

- Seus testes comparativos de EasySMX X20 v1.18 e v1.21, mesmo HWID,
  sugerem reutilização de fluxo de chave *por seção*.
- Fluxos de chaves de HWIDs diferentes não podem ser
  simplesmente usados para descriptografar o G3V2.
- O artefato X20 tem importância metodológica; não é uma
  chave universal. As duas imagens X20 ainda não estavam
  publicadas naquela discussão.
- Outros testes da família (incluindo AES, SM4, ChaCha20 e RC4)
  não recuperaram o plaintext; não repetir uma bateria
  idêntica de brute-force genérico sem nova evidência.

Pesquisa independente:
`https://github.com/LopeKinz/Manba-One-V2-Firmware-tools/issues/2`
(issue de 2026-10-08, EstatorMator, ZXD2271).

**Gamepadla** registra G3 V2 com firmware **1.25**
em testes datados de 2025-01-28:
`https://gamepadla.com/machenike-g3-v2.html`.
É evidência de uma geração anterior, **não** comprovação
de um pacote acessível, HWID idêntico ou compatibilidade
de criptografia. Não tentar flashear 1.25.

## Próximo passo único não repetido

A API atual do MixDrive:
`GET https://mixdriver.thunderobot.com/api/firmware/by-product-info?brand=MACHENIKE&product_name=G3V2&product_type=2`
retorna somente o mais recente: v1.48, product id 8,
firmware id 11. A URL atual inclui `uniqid()` em prefixo:
não se deve adivinhar a URL histórica pela versão.

Já existe um **probe histórico GET-only** no repositório:
`tools/probe-firmware-history-api.py`.
O script testa oito rotas plausíveis (id/list/history);
**foi preparado, mas não há resultado de execução documentado**.
Executá-lo no ambiente do usuário é a próxima checagem de
baixo custo, sem tocar no controlador.

Depois do resultado:
- Se aparecer uma URL de firmware anterior: fazer *download
  local somente*, registrar SHA256, extrair/validar FOT/HWID,
  comparar XCOD e XRES por offsets de seção.
- Se retornar 404/sem histórico: **não** ficar testando
  variações intermináveis; procurar arquivo original em
  canais oficiais, caches públicos e acervos, ou solicitar
  versão antiga ao fabricante/pesquisadores.
- Só comparar payloads de **mesmo HWID**. Diferentes HWIDs
  podem usar keystreams incompatíveis.
- Mesmo com duas versões, XOR de ciphertext é
  `P1 XOR P2` se a keystream for reutilizada. É evidência
  diferencial, **não plaintext imediato**. Cribs precisam
  ser fundamentados em formatos/instruções previamente
  verificados, não em assinaturas aleatórias.
- Decriptação total exige conhecida implementação de
  cifra+chave/derivação ou plaintext suficiente para
  recuperar uma keystream reutilizada. Se não houver nova
  evidência, reconhecer o limite.

## Ferramentas já existentes (não duplicar)

- `tools/get-g3v2-firmware.py`: consulta do latest e download
  oficial, sem executar updater.
- `tools/probe-firmware-history-api.py`: GET-only histórico.
- `tools/scan-g3v2-local-fot-corpus.py`: localiza imagens FOT.
- `tools/find-zxd2267-firmware-corpus.py`: busca artefatos
  exatos do HWID e nomes de firmware.
- `tools/analyze-g3v2-fot-crypto-surface.py`:
  entropia/estrutura de uma imagem.
- `tools/compare-g3v2-same-hwid-fots.py`:
  compara *duas imagens do mesmo layout*, mas sua versão
  atual exige tamanhos e offsets equivalentes; uma
  imagem histórica com layout diferente exigirá
  comparação baseada nos limites reais das seções.
- `tools/inspect-g3v2-fot-header-delta.py`: mudanças no header.

## Guardrails

Nenhum teste dessa trilha precisa de USB, BLE, root,
`rpm-ostree`, bootloader, SCSI F0/F1/F2, gravações
0x37/0x3B, instalação de ferramentas do sistema ou
alterações ao firmware do G3V2.

Não publicar MACs, identificadores de série pessoais,
caminhos privados, conteúdo binário proprietário sem
autorização ou outros dados únicos da unidade.
