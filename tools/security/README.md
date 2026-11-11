# Auditoria de segurança do Cruz Fit

O Codex Security fica nesta pasta como ferramenta de desenvolvimento. No
PowerShell, abra a raiz do projeto. Use Node.js 24 ou 26, ou Node.js 22.13
ou posterior da série 22. A análise também requer Python 3.10 ou posterior.
Com Python 3.10, instale `tomli` no ambiente que a ferramenta utilizará.

## Instalar e entrar

```powershell
$env:NODE_USE_SYSTEM_CA = '1'
npm ci --prefix tools/security --ignore-scripts
npm --prefix tools/security run codex-security -- --version
npm --prefix tools/security run codex-security -- login
```

Conclua o login com a conta ChatGPT autorizada para a auditoria.

## Analisar uma cópia do código

Este bloco cria uma cópia da versão registrada no Git. O banco, as imagens,
os backups e as configurações locais ignoradas pelo Git ficam fora da cópia.
Guarde os relatórios no diretório privado criado para cada auditoria.

```powershell
& {
    $repo = (Resolve-Path .).Path
    $auditoria = Join-Path $env:LOCALAPPDATA ("CruzFit\Auditorias\" + [guid]::NewGuid().ToString('N'))
    $copia = Join-Path $auditoria 'codigo'
    $relatorio = Join-Path $auditoria 'relatorio'
    New-Item -ItemType Directory -Path $relatorio -Force -ErrorAction Stop | Out-Null
    git clone --no-local --depth 1 --single-branch -- "$repo" "$copia"
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar a cópia da auditoria.' }
    npm --prefix tools/security run codex-security -- scan "$copia" --auth chatgpt --output-dir "$relatorio" --dry-run
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao verificar os parâmetros da auditoria.' }
    npm --prefix tools/security run codex-security -- scan "$copia" --auth chatgpt --output-dir "$relatorio"
    if ($LASTEXITCODE -ne 0) { throw "Confira a saída e os arquivos em $relatorio." }
    Join-Path $relatorio 'report.md'
}
```

Revise os achados do `report.md` antes de aplicar correções. Para reproduzir
um achado que exija o Cruz Fit em execução, use uma instalação descartável
com banco próprio e usuários fictícios. Registre o impacto, os passos para
reproduzir e o resultado da repetição após corrigir.

[Documentação oficial do Codex Security CLI](https://learn.chatgpt.com/docs/security/cli)
