# G3 V2 — novos testes RGB e persistência (21/11/2026)

Fonte: exportação de conversa PDF de 88 páginas entregue pelo usuário. Os testes abaixo foram realizados anteriormente pelo usuário; este registro não executa nenhuma operação no dispositivo nem reproduz o PDF ou seus dados pessoais.

## 1. Cor de B7 preservada com LED apagado

O usuário aplicou VERDE via KeyLinker antes de colocar o controle na base. Depois de horas na base, a iluminação estava apagada. Três leituras BLE sucessivas fora da base retornaram os mesmos seis bytes: 15 F6 05 00 00 FF (RGB 21,246,5; brilho 255), apesar dos LEDs fisicamente desligados. Ao pressionar FN+Direita, acendeu o efeito rainbow.

Resultado medido: a cor base armazenada no B7 sobreviveu àquele período e pode existir enquanto os LEDs estão apagados; estado visual, cor B7 e efeito local são distintos.

Não demonstrado: se B7 está na Flash, RAM mantida, EEPROM emulada ou foi recarregado. Não afirmar que a base substituiu ciano por verde: o verde já havia sido escolhido pelo usuário antes.

## 2. Aplicar cor no app interrompe rainbow

O usuário selecionou roxo no aplicativo Android KeyLinker e clicou Aplicar enquanto o modo rainbow estava ligado. O efeito mudou visivelmente para ROXO SÓLIDO. Leitura subsequente via BLE: B7=80 00 FF 00 00 FF, RGB(128,0,255), brilho 255.

Confirmado: a aplicação explícita de cor altera B7 e força o estado visível sólido mesmo partindo de rainbow. O aplicativo não muda cor continuamente ao arrastar o seletor, segundo observação direta do usuário. Isso não demonstra uma via RGB volátil de alta frequência. O opcode 0x25 tem evidência de família para Turbo, não para cor volátil.

## 3. Timing USB de 0x37: teste v1 anterior

Quatro escritas isoladas, com restauração final:
- B7 base mediana 4,00 ms;
- 0x37 até ACK: 3,84 / 9,94 / 7,95 / 9,97 ms, mediana 8,95 ms;
- B7 após: 4,01 / 30,00 / 6,00 / 4,01 ms.
Nenhuma inferência definitiva sobre Flash; não repetir v1.

## 4. Teste novo v2 — 0x37 valor IGUAL vs MUDANDO

O usuário executou um comparativo alternando branco atual FF FF FF 00 00 FF e vermelho marcador FF 00 00 00 00 FF, com oito observações IGUAL e oito MUDANDO, mais uma escrita igual de aquecimento descartada (17 escritas relatadas ao todo). O estado B7 final coincidiu com o branco inicial, e o snapshot foi removido.

| Estatística | IGUAL (n=8) | MUDANDO (n=8) |
|---|---:|---:|
| ACK mediana | 5,88 ms | 6,88 ms |
| ACK média | 8,64 ms | 6,38 ms |
| ACK mínimo/máximo | 3,88 / 31,89 ms | 3,86 / 7,90 ms |
| B7 posterior mediana | 5,02 ms | 8,02 ms |
| B7 posterior média | 5,52 ms | 7,02 ms |

Teste de permutação registrado: diferença de médias do ACK MUDANDO−IGUAL = −2,26 ms, p≈0,830; diferença de médias da leitura B7 posterior = +1,51 ms, p≈0,076.

Conclusão: não foi demonstrado caminho de gravação diferente quando RGB muda; diferenças da leitura posterior são inconclusivas. Um ACK igual levou 31,89 ms, distorcendo a média. Temporização USB quantizada na escala de aproximadamente 2 ms. Testes de timing por repetição têm baixo rendimento adicional. Não recomendar outros 0x37 em massa para investigar desgaste.

## 5. Alerta de segurança sobre o script v2 não versionado

A exportação mostra uma linha de execução com a opção --dry-run próxima de logs que contêm transmissões reais HID 0x37. Não é possível afirmar, sem examinar a fonte e a origem exata da saída, se --dry-run falhou ou se o log foi colado de uma execução posterior. O arquivo g3v2-light-write-timing-v2.py não está no repositório GitHub na data desta análise. NÃO presumir que o argumento --dry-run seja seguro; auditar o arquivo offline antes de qualquer utilização.

## 6. Situação para Ambilight

Comunicação e leitura B7 por BLE confirmadas (roundtrip anterior 21–23 ms). Aplicar cor no app altera B7 e cor sólida. Escrita RGB de streaming seguro, volátil e comprovadamente sem desgaste: NÃO identificada. Durabilidade da Flash ZC32RF1M desconhecida; qualquer orçamento com 10k/100k ciclos é hipótese, não garantia. Não transmitir 0x37 em taxas de quadros. A escolha de breathing e velocidade no app foi observada, mas ainda não há teste pareado B7 que confirme o mapeamento vivo desses campos. Este relatório não recupera plaintext do FOT 1.48/1.59.
