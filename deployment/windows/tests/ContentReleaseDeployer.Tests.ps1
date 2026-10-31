BeforeAll {
    . (Join-Path $PSScriptRoot '../lib/ContentReleaseDeployer.ps1')
    $script:git = (Get-Command git).Source
    $script:keygen = Join-Path $env:SystemRoot 'System32/OpenSSH/ssh-keygen.exe'

    function New-Repository {
        $dir = Join-Path $TestDrive ([guid]::NewGuid().ToString('n'))
        New-Item -ItemType Directory -Path $dir | Out-Null
        foreach ($a in @(@('init', '--quiet'), @('config', 'user.email', 't@example.invalid'), @('config', 'user.name', 'Test'),
                         @('config', 'commit.gpgsign', 'false'), @('config', 'tag.gpgsign', 'false'), @('config', 'core.autocrlf', 'false'))) {
            Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $dir -Arguments $a) 'setup' | Out-Null
        }
        Set-Content -LiteralPath (Join-Path $dir 'requirements.txt') -Value 'pytest' -NoNewline
        Set-Content -LiteralPath (Join-Path $dir 'data.json') -Value 'v1' -NoNewline
        Commit -Dir $dir -Message 'first' | Out-Null
        return $dir
    }
    function Commit([string]$Dir, [string]$Message) {
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $Dir -Arguments @('add', '-A')) 'add' | Out-Null
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $Dir -Arguments @('commit', '--quiet', '-m', $Message)) 'commit' | Out-Null
        (Invoke-ContentGit -Git $script:git -App $Dir -Arguments @('rev-parse', 'HEAD')).Stdout.Trim()
    }
    function New-SigningKey([string]$Name) {
        $key = Join-Path $TestDrive "$Name-$([guid]::NewGuid().ToString('n'))"
        $r = Invoke-ContentNative -FilePath $script:keygen -ArgumentList @('-q', '-t', 'ed25519', '-N', '', '-C', $Name, '-f', $key)
        if ($r.ExitCode) { throw "ssh-keygen failed: $($r.Stderr)" }
        return $key
    }
    function New-SignedTag([string]$Dir, [string]$Tag, [string]$Key) {
        $pub = "$Key.pub".Replace('\', '/')
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $Dir -Arguments @('-c', 'gpg.format=ssh', '-c', "user.signingkey=$pub",
            'tag', '-s', '-m', $Tag, $Tag)) 'signed tag' | Out-Null
    }
    function New-Signers([string]$Name, [string]$Key) {
        $file = Join-Path $TestDrive "signers-$([guid]::NewGuid().ToString('n'))"
        $parts = (Get-Content "$Key.pub" -Raw).Trim().Split(' ')
        Set-Content -LiteralPath $file -Value "$Name namespaces=`"git`" $($parts[0]) $($parts[1])" -Encoding ASCII
        return $file
    }
}

Describe 'Release versions' {
    It 'orders release candidates before their final release' {
        $tags = 'v1.0.0-rc9', 'v1.0.0-rc10', 'v1.0.0', 'v1.0.1-rc1', 'v0.9.9'
        Select-ContentReleaseCandidate -Tags $tags -Current 'v1.0.0-rc9' | Should -Be 'v1.0.1-rc1'
        Select-ContentReleaseCandidate -Tags ('v1.0.0-rc9', 'v1.0.0-rc10') -Current 'v1.0.0-rc9' | Should -Be 'v1.0.0-rc10'
        Select-ContentReleaseCandidate -Tags ('v1.0.0-rc10', 'v1.0.0') -Current 'v1.0.0-rc10' | Should -Be 'v1.0.0'
    }

    It 'never goes back to an older release and ignores non-release tags' {
        Select-ContentReleaseCandidate -Tags ('v1.0.0-rc7', 'experiment', 'v2', 'v1.0.0-rc8x') -Current 'v1.0.0-rc8' | Should -BeNullOrEmpty
    }

    It 'skips tags already turned down' {
        Select-ContentReleaseCandidate -Tags ('v1.0.0-rc10', 'v1.0.0-rc11') -Current 'v1.0.0-rc9' -Rejected 'v1.0.0-rc11' | Should -Be 'v1.0.0-rc10'
    }

    It 'finds nothing when the running tree is not a release' {
        Select-ContentReleaseCandidate -Tags ('v1.0.0-rc10') -Current '' | Should -BeNullOrEmpty
    }
}

Describe 'Release signatures' {
    BeforeAll {
        $script:repo = New-Repository
        $script:operator = New-SigningKey 'operator'
        $script:intruder = New-SigningKey 'intruder'
        $script:signers = New-Signers 'operator' $script:operator
    }

    It 'accepts a tag signed by the pinned signer' {
        New-SignedTag -Dir $script:repo -Tag 'v1.0.0-rc1' -Key $script:operator
        (Test-ContentReleaseSignature -Git $script:git -App $script:repo -Tag 'v1.0.0-rc1' -SignersFile $script:signers -Signer 'operator').Valid |
            Should -BeTrue
    }

    It 'refuses a tag signed by any other key' {
        New-SignedTag -Dir $script:repo -Tag 'v1.0.0-rc2' -Key $script:intruder
        (Test-ContentReleaseSignature -Git $script:git -App $script:repo -Tag 'v1.0.0-rc2' -SignersFile $script:signers -Signer 'operator').Valid |
            Should -BeFalse
    }

    It 'refuses unsigned and lightweight tags' {
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $script:repo -Arguments @('tag', '-a', '-m', 'x', 'v1.0.0-rc3')) 'tag' | Out-Null
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $script:repo -Arguments @('tag', 'v1.0.0-rc4')) 'tag' | Out-Null
        foreach ($tag in 'v1.0.0-rc3', 'v1.0.0-rc4') {
            (Test-ContentReleaseSignature -Git $script:git -App $script:repo -Tag $tag -SignersFile $script:signers -Signer 'operator').Valid |
                Should -BeFalse
        }
    }

    It 'refuses a valid signature under another signer name' {
        New-SignedTag -Dir $script:repo -Tag 'v1.0.0-rc5' -Key $script:operator
        (Test-ContentReleaseSignature -Git $script:git -App $script:repo -Tag 'v1.0.0-rc5' -SignersFile $script:signers -Signer 'someone-else').Valid |
            Should -BeFalse
    }
}

Describe 'Tree update' {
    BeforeEach {
        $script:repo = New-Repository
        $script:first = (Invoke-ContentGit -Git $script:git -App $script:repo -Arguments @('rev-parse', 'HEAD')).Stdout.Trim()
        $script:python = Join-Path $TestDrive 'python.cmd'
        Set-Content -LiteralPath $script:python -Value '@echo pip %* > "%~dp0pip-called.txt"' -Encoding ASCII
        Remove-Item -LiteralPath (Join-Path $TestDrive 'pip-called.txt') -ErrorAction SilentlyContinue
    }

    It 'checks the release out detached and installs requirements only when they changed' {
        Set-Content -LiteralPath (Join-Path $script:repo 'code.py') -Value 'x = 1'
        $second = Commit -Dir $script:repo -Message 'code'
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $script:repo -Arguments @('checkout', '--quiet', '--detach', $script:first)) 'reset' | Out-Null
        $r = Update-ContentTree -Git $script:git -App $script:repo -Target $second -Python $script:python
        $r.RequirementsChanged | Should -BeFalse
        Test-Path (Join-Path $TestDrive 'pip-called.txt') | Should -BeFalse
        (Get-Content (Join-Path $script:repo '.git/HEAD') -Raw).Trim() | Should -Be $second  # what the launcher reads

        Set-Content -LiteralPath (Join-Path $script:repo 'requirements.txt') -Value 'pytest==9' -NoNewline
        $third = Commit -Dir $script:repo -Message 'deps'
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $script:repo -Arguments @('checkout', '--quiet', '--detach', $second)) 'reset' | Out-Null
        (Update-ContentTree -Git $script:git -App $script:repo -Target $third -Python $script:python).RequirementsChanged | Should -BeTrue
        Test-Path (Join-Path $TestDrive 'pip-called.txt') | Should -BeTrue
    }

    It 'keeps a production data change the release does not touch' {
        Set-Content -LiteralPath (Join-Path $script:repo 'code.py') -Value 'x = 1'
        $second = Commit -Dir $script:repo -Message 'code'
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $script:repo -Arguments @('checkout', '--quiet', '--detach', $script:first)) 'reset' | Out-Null
        Set-Content -LiteralPath (Join-Path $script:repo 'data.json') -Value 'production' -NoNewline
        Update-ContentTree -Git $script:git -App $script:repo -Target $second -Python $script:python | Out-Null
        Get-Content (Join-Path $script:repo 'data.json') -Raw | Should -Be 'production'
    }

    It 'stops when production changed a file the release changes differently' {
        Set-Content -LiteralPath (Join-Path $script:repo 'data.json') -Value 'release' -NoNewline
        $second = Commit -Dir $script:repo -Message 'data'
        Assert-ContentGit (Invoke-ContentGit -Git $script:git -App $script:repo -Arguments @('checkout', '--quiet', '--detach', $script:first)) 'reset' | Out-Null
        Set-Content -LiteralPath (Join-Path $script:repo 'data.json') -Value 'production' -NoNewline
        { Update-ContentTree -Git $script:git -App $script:repo -Target $second -Python $script:python } | Should -Throw '*differently*'
        (Invoke-ContentGit -Git $script:git -App $script:repo -Arguments @('rev-parse', 'HEAD')).Stdout.Trim() | Should -Be $script:first
    }
}

Describe 'Helpers' {
    It 'reads ISO timestamps as UTC whatever the local time zone' {
        (ConvertTo-ContentUtc '2026-10-23T18:00:00.0000000Z') | Should -Be ([datetime]::new(2026, 10, 23, 18, 0, 0, [DateTimeKind]::Utc))
        (ConvertTo-ContentUtc $null) | Should -Be ([datetime]::MinValue)
    }

    It 'passes arguments with spaces and quotes to native commands intact' {
        $r = Invoke-ContentNative -FilePath $env:ComSpec -ArgumentList @('/d', '/c', 'echo', 'a b')
        $r.Stdout.Trim() | Should -Be '"a b"'
    }
}
