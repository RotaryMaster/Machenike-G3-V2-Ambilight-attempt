# Referências de resolução de problemas — Machenike G3 V2

## Relato externo: G5 Pro com gatilhos travados e 2,4 GHz indisponível

- Fonte: Reddit, r/Controller, "[SOLVED] MACHENIKE G5 Pro - Triggers permanently pressed after calibration / 2.4GHz stopped working (Complete Fix)".
- URL: https://www.reddit.com/r/Controller/comments/1v0dmci/solved_machenike_g5_pro_triggers_permanently/
- Registrado em: 2026-11-21, a pedido do proprietário do G3 V2, como referência para **problemas futuros**.
- Modelo do relato: Machenike **G5 Pro tri-mode**, NÃO G3 V2.

### Problema relatado

Após calibração no software oficial, USB e Bluetooth ainda funcionavam, porém a comunicação via receptor 2,4 GHz tornou-se inutilizável. LT/RT eram identificados pelo Gamepad Tester como permanentemente pressionados (B6=1.00 e B7=1.00). Em uma etapa posterior, o controlador ainda emparelhava ao receptor, mas nenhum dos botões/analógicos respondia.

Tentativas que o autor disse não resolverem: reset pelo software, calibração, calibração do Windows, novo pareamento, outras portas USB e outros PCs.

### Recuperação relatada para o G5 Pro

O autor localizou no portal oficial **chinês** da Machenike um pacote diferente do catálogo internacional, com **firmwares separados para o controle e para o dongle 2,4 GHz**. Reinstalar as imagens respectivas de seu G5 Pro tri-mode resolveu o problema segundo o relato; comentários de outros usuários também reportaram sucesso.

- Portal indicado: https://www.machenike.com/index.php/driver.php
- **Aviso crítico:** o próprio autor diferencia arquivos G5 Pro tri-mode e dual-mode. Não usar pacotes da versão errada.
- A explicação de *incompatibilidade de versões entre controle e receptor após calibração* é a **hipótese do autor**, não um defeito universalmente comprovado.

### Relevância para nosso G3 V2

Já confirmamos independentemente que o pacote oficial do **G3 V2**
(https://www.machenike.com/driver/download/?id=190)
contém dois atualizadores diferentes: **MKG_FW_G3V2.exe** (controle; firmware FOT 1.59 investigado) e **MKG_FW_G3V2.Receiver.exe** (receptor). Não confundir os dois.

Se o G3 V2 apresentar falhas de gatilhos, perder comandos em 2,4 GHz, continuar funcionando por USB/Bluetooth, ou apresentar problemas após calibração/atualização, usar esse caso como **pista diagnóstica** sobre estado/compatibilidade entre receptor e controle.

**Nunca aplicar os EXEs, modo de recuperação, atalhos ou firmware do G5 Pro ao G3 V2. Não executar reflash preventivo.**

### Procedimento conservador se aparecer defeito no futuro

1. Confirmar sintoma, início e modos afetados (USB, 2,4 GHz, Bluetooth); diferenciar gatilhos realmente pressionados de ausência de comunicação.
2. Observar em um testador de gamepad e checar identidades, versões e logs do receptor e controle, prioritariamente **somente leitura**, em Linux Bazzite KDE e/ou Windows.
3. Investigar primeiro estado do dongle, seleção de modo e pareamento, em ações reversíveis, sem modificar a base imutável.
4. Conferir somente firmwares **G3 V2** com hardware ID apropriado, acompanhados dos respectivos hashes e instruções oficiais, separando firmware do dongle e do controle.
5. Considerar reflashing como **último recurso**, sujeito a confirmação de compatibilidade e consentimento explícito, pois pode inutilizar o hardware.

Esta página arquiva uma possível solução para **uso futuro**. Ela não documenta defeito atual no controle do usuário.
