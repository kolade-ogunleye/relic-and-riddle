BeforeAll {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:runtimePlay = Get-Content (Join-Path $repoRoot 'ansible/playbooks/52-content-runtime.yml') -Raw
    $script:site = Get-Content (Join-Path $repoRoot 'ansible/playbooks/site.yml') -Raw
}

Describe 'Content runtime deployment gates' {
    It 'allows SQLite sidecar creation without granting backup write access to the existing database' {
        $script:runtimePlay | Should -Match 'rights: CreateFiles'
        $script:runtimePlay | Should -Match 'user: S-1-3-0'
        $script:runtimePlay | Should -Match 'propagation: InheritOnly'
    }
    It 'defaults to canaries and does not start or stop production from configuration' {
        $script:runtimePlay | Should -Match 'content_unattended_enabled \| default\(false\)'
        $script:runtimePlay | Should -Not -Match 'Start-ScheduledTask|Stop-ScheduledTask|Restart-Computer|Copy-Item.*\.env'
        $script:runtimePlay | Should -Match "'Production'.*'Canary'"
    }

    It 'keeps secret grants silent and uses a separate managed token cache' {
        $script:runtimePlay | Should -Match 'no_log: true'
        $script:runtimePlay | Should -Match 'source_token_cache:'
        $script:runtimePlay | Should -Match 'token_cache: C:.*secrets\\Backup\\tokens.enc'
        $script:runtimePlay | Should -Not -Match 'Get-Content.*\.env|Get-Content.*\.key|Get-Content.*tokens-files'
    }

    It 'keeps code read-only to the runtime accounts while allowing scoped data writes' {
        $script:runtimePlay | Should -Match 'rights: ReadAndExecute'
        $script:runtimePlay | Should -Match 'Grant only pipeline data directories write access'
        $script:runtimePlay | Should -Match 'Remove transitional backup write access'
        $script:runtimePlay | Should -Match "'spool\\Backup'.*'Modify'"
    }

    It 'imports application configuration after the identity prerequisites' {
        $script:site.IndexOf('52-content-runtime.yml') | Should -BeGreaterThan $script:site.IndexOf('51-content-identity-probes.yml')
    }
}
