# Rate Limiting 테스트 PowerShell 스크립트

param(
    [Parameter(Mandatory=$true)]
    [string]$ApiKey,
    
    [int]$NumRequests = 5,
    [double]$Delay = 0.5
)

$endpoint = "https://api.realcatcha.com/api/next-captcha"

Write-Host "🚀 Rate Limiting 테스트 시작" -ForegroundColor Green
Write-Host "📡 엔드포인트: $endpoint" -ForegroundColor Cyan
Write-Host "🔑 API 키: $($ApiKey.Substring(0, 20))..." -ForegroundColor Yellow
Write-Host "📊 요청 수: $NumRequests" -ForegroundColor Magenta
Write-Host "⏱️ 요청 간격: $Delay초" -ForegroundColor Magenta
Write-Host ("-" * 60) -ForegroundColor Gray

$headers = @{
    'X-API-Key' = $ApiKey
    'Content-Type' = 'application/json'
}

$payload = @{
    session_id = 'test_session'
    captcha_type = 'imagegrid'
} | ConvertTo-Json

$results = @()

for ($i = 1; $i -le $NumRequests; $i++) {
    Write-Host "📤 요청 $i/$NumRequests 전송 중..." -ForegroundColor Blue
    
    $startTime = Get-Date
    
    try {
        $response = Invoke-RestMethod -Uri $endpoint -Method Post -Headers $headers -Body $payload -TimeoutSec 10
        $responseTime = ((Get-Date) - $startTime).TotalSeconds
        
        $result = @{
            RequestId = $i
            StatusCode = 200
            ResponseTime = $responseTime
            Success = $true
            ResponseData = ($response | ConvertTo-Json -Depth 2).Substring(0, 200)
        }
        
        $results += $result
        Write-Host "✅ 요청 $i`: 성공 ($($responseTime.ToString('F3'))초)" -ForegroundColor Green
        
    } catch {
        $responseTime = ((Get-Date) - $startTime).TotalSeconds
        $statusCode = 0
        $errorMessage = $_.Exception.Message
        
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        
        $result = @{
            RequestId = $i
            StatusCode = $statusCode
            ResponseTime = $responseTime
            Success = $false
            Error = $errorMessage
            ResponseData = ""
        }
        
        $results += $result
        
        if ($statusCode -eq 429) {
            Write-Host "🚫 요청 $i`: Rate Limited ($($responseTime.ToString('F3'))초)" -ForegroundColor Red
            try {
                $errorResponse = $_.Exception.Response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($errorResponse)
                $errorBody = $reader.ReadToEnd()
                Write-Host "   상세: $($errorBody.Substring(0, 100))..." -ForegroundColor Yellow
            } catch {
                Write-Host "   오류: $errorMessage" -ForegroundColor Yellow
            }
        } else {
            Write-Host "❌ 요청 $i`: 실패 - $statusCode ($($responseTime.ToString('F3'))초)" -ForegroundColor Red
            Write-Host "   오류: $errorMessage" -ForegroundColor Yellow
        }
    }
    
    # 요청 간격 대기
    if ($i -lt $NumRequests) {
        Start-Sleep -Seconds $Delay
    }
}

# 결과 요약
Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Gray
Write-Host "📊 테스트 결과 요약" -ForegroundColor Green
Write-Host ("=" * 60) -ForegroundColor Gray

$successfulRequests = $results | Where-Object { $_.Success -eq $true }
$failedRequests = $results | Where-Object { $_.Success -eq $false }
$rateLimitedRequests = $results | Where-Object { $_.StatusCode -eq 429 }

Write-Host "✅ 성공한 요청: $($successfulRequests.Count)/$NumRequests" -ForegroundColor Green
Write-Host "❌ 실패한 요청: $($failedRequests.Count)/$NumRequests" -ForegroundColor Red
Write-Host "🚫 Rate Limited: $($rateLimitedRequests.Count)/$NumRequests" -ForegroundColor Yellow

if ($successfulRequests.Count -gt 0) {
    $avgResponseTime = ($successfulRequests | Measure-Object -Property ResponseTime -Average).Average
    Write-Host "⏱️ 평균 응답 시간: $($avgResponseTime.ToString('F3'))초" -ForegroundColor Cyan
}

if ($rateLimitedRequests.Count -gt 0) {
    Write-Host ""
    Write-Host "🚫 Rate Limiting 상세:" -ForegroundColor Yellow
    foreach ($req in $rateLimitedRequests) {
        Write-Host "   요청 $($req.RequestId): $($req.ResponseData.Substring(0, 100))..." -ForegroundColor Yellow
    }
}

# Burst 테스트
Write-Host ""
Write-Host "💥 Burst 테스트 시작 (동시 요청 3개)" -ForegroundColor Magenta
Write-Host ("-" * 60) -ForegroundColor Gray

$burstResults = @()
$burstPayload = @{
    session_id = 'test_burst_session'
    captcha_type = 'imagegrid'
} | ConvertTo-Json

for ($i = 1; $i -le 3; $i++) {
    Write-Host "📤 Burst 요청 $i/3 전송 중..." -ForegroundColor Blue
    
    $startTime = Get-Date
    
    try {
        $response = Invoke-RestMethod -Uri $endpoint -Method Post -Headers $headers -Body $burstPayload -TimeoutSec 10
        $responseTime = ((Get-Date) - $startTime).TotalSeconds
        
        $result = @{
            RequestId = $i
            StatusCode = 200
            ResponseTime = $responseTime
            Success = $true
        }
        
        $burstResults += $result
        Write-Host "✅ Burst 요청 $i`: 성공 ($($responseTime.ToString('F3'))초)" -ForegroundColor Green
        
    } catch {
        $responseTime = ((Get-Date) - $startTime).TotalSeconds
        $statusCode = 0
        
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        
        $result = @{
            RequestId = $i
            StatusCode = $statusCode
            ResponseTime = $responseTime
            Success = $false
        }
        
        $burstResults += $result
        
        if ($statusCode -eq 429) {
            Write-Host "🚫 Burst 요청 $i`: Rate Limited" -ForegroundColor Red
        } else {
            Write-Host "❌ Burst 요청 $i`: 실패 - $statusCode" -ForegroundColor Red
        }
    }
}

$burstSuccessful = ($burstResults | Where-Object { $_.Success -eq $true }).Count
$burstRateLimited = ($burstResults | Where-Object { $_.StatusCode -eq 429 }).Count

Write-Host ""
Write-Host "📊 Burst 테스트 결과: 성공 $burstSuccessful, Rate Limited $burstRateLimited, 실패 $($3 - $burstSuccessful - $burstRateLimited)" -ForegroundColor Cyan


