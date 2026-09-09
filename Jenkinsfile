pipeline {

    agent any

    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(
            logRotator(
                numToKeepStr: '10'
            )
        )
    }

    parameters {

        choice(
            name: 'DEMO_SCENARIO',
            choices: [
                'ALL_STAGES_PROMOTE',
                'ROLLBACK_AT_50'
            ],
            description: 'Select the AI Canary deployment scenario.'
        )

        string(
            name: 'REPORT_RECIPIENTS',
            defaultValue: '',
            description: 'Comma-separated email recipients for the final HTML report.'
        )
    }

    environment {
        PYTHONUNBUFFERED = '1'
    }

    stages {

        // ====================================================
        // 1. ENVIRONMENT SETUP
        // ====================================================

        stage('Environment Setup') {

            steps {

                echo 'Preparing AI Canary deployment environment...'

                checkout scm

                script {

                    // ------------------------------------------------
                    // Best-effort cleanup from any previous run.
                    // This is intentionally NOT part of the new
                    // pipeline timing history.
                    // ------------------------------------------------

                    def preCleanupCode = powershell(
                        returnStatus: true,
                        script: '''
                            & ./jenkins/scripts/cleanup-environment.ps1
                        '''
                    )

                    if (preCleanupCode != 0) {
                        echo "[WARN] Pre-run cleanup returned exit code ${preCleanupCode}."
                    }
                }

                powershell '''
                    if (Test-Path ./runtime) {
                        Remove-Item ./runtime -Recurse -Force
                    }

                    foreach ($file in @(
                        "./ai-engine/ai_decision.json",
                        "./ai-engine/ai_risk_context.json",
                        "./ai-engine/ai_metrics.prom",
                        "./ai-engine/ai_test.prom"
                    )) {
                        if (Test-Path $file) {
                            Remove-Item $file -Force
                        }
                    }

                    Write-Host "[PASS] Previous runtime artifacts cleared."
                '''

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "Environment Setup" `
                        -Action START
                '''

                powershell '''
                    & ./jenkins/scripts/validate-environment.ps1
                '''

                powershell '''
                    & ./jenkins/scripts/create-cluster.ps1 `
                        -RecreateExisting
                '''
            }

            post {

                success {
                    script {
                        def rc = powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Environment Setup" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )

                        if (rc != 0) {
                            echo '[WARN] Unable to close Environment Setup timing.'
                        }
                    }
                }

                failure {
                    script {
                        def rc = powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Environment Setup" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )

                        if (rc != 0) {
                            echo '[WARN] Unable to close Environment Setup timing.'
                        }
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Environment Setup" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }

        // ====================================================
        // 2. PLATFORM SETUP
        // ====================================================

        stage('Platform Setup') {

            steps {

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "Platform Setup" `
                        -Action START
                '''

                powershell '''
                    & ./jenkins/scripts/install-platform.ps1
                '''

                powershell '''
                    $BuildId = (
                        (Get-Date -Format "yyyyMMdd-HHmmss") +
                        "-" +
                        $env:BUILD_NUMBER
                    )

                    Write-Host "Jenkins Build ID : $BuildId"

                    & ./jenkins/scripts/load-images.ps1 `
                        -BuildId $BuildId
                '''
            }

            post {

                success {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Platform Setup" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )
                    }
                }

                failure {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Platform Setup" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Platform Setup" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }

        // ====================================================
        // 3. STABLE BASELINE
        // ====================================================

        stage('Stable Baseline') {

            steps {

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "Stable Baseline" `
                        -Action START
                '''

                powershell '''
                    & ./jenkins/scripts/deploy-stable.ps1
                '''

                powershell '''
                    & ./jenkins/scripts/start-traffic.ps1
                '''

                powershell '''
                    $Config = Get-Content `
                        ./jenkins/config/pipeline-config.json `
                        -Raw |
                        ConvertFrom-Json

                    $Delay = [int]$Config.demo.normal_stage_delay_seconds

                    if ($Delay -gt 0) {
                        Write-Host "Baseline observation hold: $Delay second(s)"
                        Start-Sleep -Seconds $Delay
                    }
                '''
            }

            post {

                success {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Stable Baseline" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )
                    }
                }

                failure {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Stable Baseline" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Stable Baseline" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }

        // ====================================================
        // 4. MONITORING DASHBOARD
        // ====================================================

        stage('Monitoring Dashboard') {

            steps {

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "Monitoring Dashboard" `
                        -Action START
                '''

                powershell '''
                    & ./jenkins/scripts/start-port-forwards.ps1
                '''

                powershell '''
                    & ./jenkins/scripts/publish-grafana-dashboard.ps1
                '''

                powershell '''
                    & ./jenkins/scripts/open-monitoring-dashboard.ps1
                '''
            }

            post {

                success {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Monitoring Dashboard" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )
                    }
                }

                failure {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Monitoring Dashboard" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Monitoring Dashboard" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }

        // ====================================================
        // 5. 20% CANARY STAGE
        // ====================================================

        stage('20% Canary Stage') {

            steps {

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "20% Canary Stage" `
                        -Action START
                '''

                powershell '''
                    Write-Host "Selected Scenario : $env:DEMO_SCENARIO"

                    & ./jenkins/scripts/deploy-canary.ps1 `
                        -Scenario $env:DEMO_SCENARIO
                '''

                powershell '''
                    $Config = Get-Content `
                        ./jenkins/config/pipeline-config.json `
                        -Raw |
                        ConvertFrom-Json

                    $Hold = [int]$Config.demo.deployment_observation_hold_seconds

                    if ($Hold -gt 0) {
                        Write-Host ""
                        Write-Host "20% checkpoint observation hold: $Hold second(s)"
                        Start-Sleep -Seconds $Hold
                    }
                '''

                powershell '''
                    & ./jenkins/scripts/get-ai-decision.ps1
                '''

                script {

                    def decision20 = powershell(
                        returnStdout: true,
                        script: '''
                            $Data = Get-Content `
                                ./ai-engine/ai_decision.json `
                                -Raw |
                                ConvertFrom-Json

                            Write-Output (
                                ([string]$Data.final_assessment.decision).
                                ToUpperInvariant()
                            )
                        '''
                    ).trim()

                    echo "AI decision at 20%: ${decision20}"

                    if (decision20 != 'PROMOTE') {

                        powershell '''
                            python ./ai-engine/deployment_controller.py --execute

                            if ($LASTEXITCODE -ne 0) {
                                exit $LASTEXITCODE
                            }
                        '''

                        error(
                            "20% checkpoint did not authorize progression. " +
                            "AI decision: ${decision20}"
                        )
                    }
                }

                powershell '''
                    $Config = Get-Content `
                        ./jenkins/config/pipeline-config.json `
                        -Raw |
                        ConvertFrom-Json

                    $Namespace = [string]$Config.project.namespace
                    $RolloutName = [string]$Config.project.rollout_name

                    $StatePath = "./runtime/scenario-state.json"

                    if (-not (Test-Path $StatePath)) {
                        Write-Host "[FAIL] scenario-state.json not found."
                        exit 1
                    }

                    $OldStepRaw = kubectl get rollout `
                        $RolloutName `
                        -n $Namespace `
                        -o jsonpath="{.status.currentStepIndex}"

                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "[FAIL] Unable to read current Argo step."
                        exit 1
                    }

                    $OldStep = [int]$OldStepRaw

                    Write-Host ""
                    Write-Host "Current Argo Step : $OldStep"
                    Write-Host "Executing AI-approved action..."

                    python ./ai-engine/deployment_controller.py --execute

                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "[FAIL] Deployment controller failed."
                        exit $LASTEXITCODE
                    }

                    Write-Host ""
                    Write-Host "Waiting for the next Canary checkpoint..."

                    $TimeoutSeconds = 180
                    $PollSeconds = 3
                    $Elapsed = 0
                    $Reached = $false

                    while ($Elapsed -lt $TimeoutSeconds) {

                        $RolloutRaw = kubectl get rollout `
                            $RolloutName `
                            -n $Namespace `
                            -o json

                        if ($LASTEXITCODE -ne 0) {
                            Write-Host "[FAIL] Unable to read Rollout."
                            exit 1
                        }

                        $Rollout = $RolloutRaw | ConvertFrom-Json

                        $Phase = [string]$Rollout.status.phase
                        $Step = [int]$Rollout.status.currentStepIndex
                        $PauseCount = @(
                            $Rollout.status.pauseConditions
                        ).Count

                        Write-Host (
                            "[INFO] Phase={0} Step={1} Pause={2} Elapsed={3}s" -f
                            $Phase,
                            $Step,
                            $PauseCount,
                            $Elapsed
                        )

                        if ($Phase -eq "Degraded") {
                            Write-Host "[FAIL] Rollout entered Degraded state."
                            exit 1
                        }

                        if (
                            $Step -gt $OldStep -and
                            $PauseCount -gt 0
                        ) {
                            $Reached = $true
                            break
                        }

                        Start-Sleep -Seconds $PollSeconds
                        $Elapsed += $PollSeconds
                    }

                    if (-not $Reached) {
                        Write-Host "[FAIL] 50% checkpoint was not reached."
                        exit 1
                    }

                    $State = Get-Content `
                        $StatePath `
                        -Raw |
                        ConvertFrom-Json

                    $State.current_checkpoint = 50
                    $State.rollout_phase = "Paused"
                    $State.ai_decision_pending = $true

                    if (
                        $State.PSObject.Properties.Name `
                        -contains "decision_pending_execution"
                    ) {
                        $State.decision_pending_execution = $false
                    }

                    $State |
                        ConvertTo-Json -Depth 10 |
                        Set-Content `
                            -Path $StatePath `
                            -Encoding UTF8

                    Write-Host ""
                    Write-Host "[PASS] Next Canary checkpoint reached."
                    Write-Host "[PASS] Runtime state advanced to 50%."
                '''
            }

            post {

                success {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "20% Canary Stage" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )
                    }
                }

                failure {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "20% Canary Stage" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "20% Canary Stage" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }

        // ====================================================
        // 6. 50% CANARY STAGE
        // ====================================================

        stage('50% Canary Stage') {

            steps {

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "50% Canary Stage" `
                        -Action START
                '''

                script {

                    if (
                        params.DEMO_SCENARIO ==
                        'ROLLBACK_AT_50'
                    ) {

                        echo 'Preparing the second scenario condition at 50%...'

                        powershell '''
                            & ./jenkins/scripts/inject-demo-fault.ps1 `
                                -DelayMs 140 `
                                -ErrorRate 0.06
                        '''
                    }
                    else {
                        echo '50% Canary checkpoint remains on normal application behavior.'
                    }
                }

                powershell '''
                    $Config = Get-Content `
                        ./jenkins/config/pipeline-config.json `
                        -Raw |
                        ConvertFrom-Json

                    $Hold = [int]$Config.demo.deployment_observation_hold_seconds

                    if ($Hold -gt 0) {
                        Write-Host ""
                        Write-Host "50% checkpoint observation hold: $Hold second(s)"
                        Start-Sleep -Seconds $Hold
                    }
                '''

                powershell '''
                    & ./jenkins/scripts/get-ai-decision.ps1
                '''

                script {

                    def decision50 = powershell(
                        returnStdout: true,
                        script: '''
                            $Data = Get-Content `
                                ./ai-engine/ai_decision.json `
                                -Raw |
                                ConvertFrom-Json

                            Write-Output (
                                ([string]$Data.final_assessment.decision).
                                ToUpperInvariant()
                            )
                        '''
                    ).trim()

                    echo "AI decision at 50%: ${decision50}"

                    /*
                     * Jenkins does not replace the AI decision.
                     * The deployment controller executes the AI-approved
                     * result exactly as produced by the decision engine.
                     */

                    powershell '''
                        python ./ai-engine/deployment_controller.py --execute

                        if ($LASTEXITCODE -ne 0) {
                            exit $LASTEXITCODE
                        }
                    '''

                    if (
                        params.DEMO_SCENARIO ==
                        'ALL_STAGES_PROMOTE' &&
                        decision50 != 'PROMOTE'
                    ) {
                        error(
                            "Scenario expected healthy progression at 50%, " +
                            "but AI selected ${decision50}."
                        )
                    }

                    if (
                        params.DEMO_SCENARIO ==
                        'ROLLBACK_AT_50' &&
                        decision50 != 'ROLLBACK'
                    ) {
                        error(
                            "Scenario expected AI recovery action at 50%, " +
                            "but AI selected ${decision50}."
                        )
                    }
                }
            }

            post {

                success {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "50% Canary Stage" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )
                    }
                }

                failure {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "50% Canary Stage" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "50% Canary Stage" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }

        // ====================================================
        // 7. FINAL VALIDATION
        // ====================================================

        stage('Final Validation') {

            steps {

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "Final Validation" `
                        -Action START
                '''

                script {

                    if (
                        params.DEMO_SCENARIO ==
                        'ALL_STAGES_PROMOTE'
                    ) {

                        echo 'Waiting for the 100% Candidate AI checkpoint...'

                        powershell '''
                            $Config = Get-Content `
                                ./jenkins/config/pipeline-config.json `
                                -Raw |
                                ConvertFrom-Json

                            $Namespace = [string]$Config.project.namespace
                            $RolloutName = [string]$Config.project.rollout_name
                            $StatePath = "./runtime/scenario-state.json"

                            if (-not (Test-Path $StatePath)) {
                                Write-Host "[FAIL] scenario-state.json not found."
                                exit 1
                            }

                            $TimeoutSeconds = 180
                            $PollSeconds = 3
                            $Elapsed = 0
                            $Reached = $false

                            while ($Elapsed -le $TimeoutSeconds) {

                                $RolloutRaw = kubectl get rollout `
                                    $RolloutName `
                                    -n $Namespace `
                                    -o json

                                if ($LASTEXITCODE -ne 0) {
                                    Write-Host "[FAIL] Unable to read Rollout at final checkpoint."
                                    exit 1
                                }

                                $Rollout = $RolloutRaw | ConvertFrom-Json
                                $State = Get-Content $StatePath -Raw | ConvertFrom-Json

                                $Phase = [string]$Rollout.status.phase
                                $PauseCount = @($Rollout.status.pauseConditions).Count
                                $Checkpoint = [int]$State.current_checkpoint
                                $CandidateVersion = [string]$State.canary_version
                                $DesiredPods = [int]$Rollout.spec.replicas

                                $PodsRaw = kubectl get pods `
                                    -n $Namespace `
                                    -l "app=$RolloutName" `
                                    -o json

                                if ($LASTEXITCODE -ne 0) {
                                    Write-Host "[FAIL] Unable to read application pods at final checkpoint."
                                    exit 1
                                }

                                $Pods = ($PodsRaw | ConvertFrom-Json).items

                                $CandidateReady = @(
                                    $Pods |
                                    Where-Object {
                                        $_.metadata.labels.version -eq $CandidateVersion -and
                                        $_.status.phase -eq "Running" -and
                                        @(
                                            $_.status.conditions |
                                            Where-Object {
                                                $_.type -eq "Ready" -and
                                                $_.status -eq "True"
                                            }
                                        ).Count -gt 0
                                    }
                                ).Count

                                Write-Host (
                                    "[INFO] Phase={0} Checkpoint={1}% Pause={2} CandidateReady={3}/{4} Elapsed={5}s" -f
                                    $Phase,
                                    $Checkpoint,
                                    $PauseCount,
                                    $CandidateReady,
                                    $DesiredPods,
                                    $Elapsed
                                )

                                if ($Phase -eq "Degraded") {
                                    Write-Host "[FAIL] Rollout entered Degraded state before final AI validation."
                                    exit 1
                                }

                                if (
                                    $Phase -eq "Paused" -and
                                    $Checkpoint -eq 100 -and
                                    $PauseCount -gt 0 -and
                                    $CandidateReady -eq $DesiredPods
                                ) {
                                    $Reached = $true
                                    break
                                }

                                Start-Sleep -Seconds $PollSeconds
                                $Elapsed += $PollSeconds
                            }

                            if (-not $Reached) {
                                Write-Host "[FAIL] 100% Candidate checkpoint was not reached with all Candidate pods Ready."
                                exit 1
                            }

                            Write-Host ""
                            Write-Host "[PASS] 100% Candidate checkpoint reached."
                            Write-Host "[PASS] Candidate pods Ready: $CandidateReady/$DesiredPods"
                            Write-Host "[PASS] Rollout remains paused for FINAL AI validation."
                        '''

                        powershell '''
                            $Config = Get-Content `
                                ./jenkins/config/pipeline-config.json `
                                -Raw |
                                ConvertFrom-Json

                            $Hold = [int]$Config.demo.deployment_observation_hold_seconds

                            if ($Hold -gt 0) {
                                Write-Host ""
                                Write-Host "100% Candidate observation hold: $Hold second(s)"
                                Start-Sleep -Seconds $Hold
                            }
                        '''

                        powershell '''
                            & ./jenkins/scripts/get-ai-decision.ps1
                        '''

                        def decision100 = powershell(
                            returnStdout: true,
                            script: '''
                                $Data = Get-Content `
                                    ./ai-engine/ai_decision.json `
                                    -Raw |
                                    ConvertFrom-Json

                                Write-Output (
                                    ([string]$Data.final_assessment.decision).
                                    ToUpperInvariant()
                                )
                            '''
                        ).trim()

                        echo "AI decision at 100%: ${decision100}"

                        powershell '''
                            python ./ai-engine/deployment_controller.py --execute

                            if ($LASTEXITCODE -ne 0) {
                                exit $LASTEXITCODE
                            }
                        '''

                        if (decision100 != 'PROMOTE') {
                            error(
                                "Final 100% Candidate checkpoint did not authorize release completion. " +
                                "AI decision: ${decision100}"
                            )
                        }

                        echo 'Final AI PROMOTE approved. Waiting for Candidate to become the new Stable.'
                    }
                    else {
                        echo 'Rollback scenario already completed its AI action at 50%; validating recovered Stable state.'
                    }
                }

                powershell '''
                    $Config = Get-Content `
                        ./jenkins/config/pipeline-config.json `
                        -Raw |
                        ConvertFrom-Json

                    $Namespace = [string]$Config.project.namespace
                    $RolloutName = [string]$Config.project.rollout_name

                    $TimeoutSeconds = 240
                    $PollSeconds = 3
                    $Elapsed = 0
                    $Healthy = $false

                    Write-Host "Waiting for final Healthy Rollout state..."

                    while ($Elapsed -lt $TimeoutSeconds) {

                        $Phase = kubectl get rollout `
                            $RolloutName `
                            -n $Namespace `
                            -o jsonpath="{.status.phase}"

                        if ($LASTEXITCODE -ne 0) {
                            Write-Host "[FAIL] Unable to read final Rollout state."
                            exit 1
                        }

                        Write-Host (
                            "[INFO] Rollout Phase={0} Elapsed={1}s" -f
                            $Phase,
                            $Elapsed
                        )

                        if ($Phase.Trim() -eq "Healthy") {
                            $Healthy = $true
                            break
                        }

                        Start-Sleep -Seconds $PollSeconds
                        $Elapsed += $PollSeconds
                    }

                    if (-not $Healthy) {
                        Write-Host "[FAIL] Final Rollout did not become Healthy."
                        exit 1
                    }

                    Write-Host "[PASS] Final Rollout is Healthy."
                '''

                powershell '''
                    & ./jenkins/scripts/capture-final-validation.ps1
                '''

                powershell '''
                    & ./jenkins/scripts/publish-final-dashboard-state.ps1
                '''

                powershell '''
                    $Config = Get-Content `
                        ./jenkins/config/pipeline-config.json `
                        -Raw |
                        ConvertFrom-Json

                    $Hold = [int]$Config.demo.final_demo_hold_seconds

                    if ($Hold -gt 0) {
                        Write-Host ""
                        Write-Host "Final dashboard observation hold: $Hold second(s)"
                        Start-Sleep -Seconds $Hold
                    }
                '''
            }

            post {

                success {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Final Validation" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )
                    }
                }

                failure {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Final Validation" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Final Validation" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }

        // ====================================================
        // 8. GENERATE REPORT
        // ====================================================

        stage('Generate Report') {

            steps {

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "Generate Report" `
                        -Action START
                '''

                powershell '''
                    python ./reporting/generate_report.py

                    if ($LASTEXITCODE -ne 0) {
                        exit $LASTEXITCODE
                    }
                '''

                powershell '''
                    $Report = (
                        "./runtime/reports/" +
                        "ai-canary-deployment-report.html"
                    )

                    if (-not (Test-Path $Report)) {
                        Write-Host "[FAIL] HTML report was not created."
                        exit 1
                    }

                    $Size = (Get-Item $Report).Length

                    if ($Size -le 0) {
                        Write-Host "[FAIL] HTML report is empty."
                        exit 1
                    }

                    Write-Host "[PASS] Detailed HTML report generated."
                    Write-Host "       Size: $Size bytes"
                '''
            }

            post {

                success {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Generate Report" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )
                    }
                }

                failure {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Generate Report" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Generate Report" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }

        // ====================================================
        // 9. CLEANUP
        // ====================================================

        stage('Cleanup') {

            steps {

                powershell '''
                    & ./jenkins/scripts/capture-stage.ps1 `
                        -Stage "Cleanup" `
                        -Action START
                '''

                powershell '''
                    & ./jenkins/scripts/cleanup-environment.ps1
                '''
            }

            post {

                success {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Cleanup" `
                                    -Action END `
                                    -Status SUCCESS
                            '''
                        )
                    }
                }

                failure {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Cleanup" `
                                    -Action END `
                                    -Status FAILED
                            '''
                        )
                    }
                }

                aborted {
                    script {
                        powershell(
                            returnStatus: true,
                            script: '''
                                & ./jenkins/scripts/capture-stage.ps1 `
                                    -Stage "Cleanup" `
                                    -Action END `
                                    -Status ABORTED
                            '''
                        )
                    }
                }
            }
        }
    }

    // ========================================================
    // PIPELINE-LEVEL SAFETY / REPORT DELIVERY
    // ========================================================

    post {

        // ----------------------------------------------------
        // GUARANTEED BACKUP CLEANUP
        // ----------------------------------------------------

        always {

            script {

                echo 'Running guaranteed post-pipeline environment cleanup...'

                def cleanupCode = powershell(
                    returnStatus: true,
                    script: '''
                        if (Test-Path ./jenkins/scripts/cleanup-environment.ps1) {
                            & ./jenkins/scripts/cleanup-environment.ps1
                        }
                        else {
                            Write-Host "[WARN] cleanup-environment.ps1 not found."
                        }
                    '''
                )

                if (cleanupCode != 0) {
                    echo(
                        "[WARN] Backup cleanup returned exit code " +
                        "${cleanupCode}. Primary pipeline result is preserved."
                    )
                }
            }
        }

        // ----------------------------------------------------
        // SUCCESS: FINALIZE, ARCHIVE AND EMAIL REPORT
        //
        // We regenerate here so pipeline-timeline.json already
        // contains Generate Report + Cleanup completion times.
        // ----------------------------------------------------

        success {

            script {

                echo 'Finalizing complete execution report...'

                powershell '''
                    python ./reporting/generate_report.py

                    if ($LASTEXITCODE -ne 0) {
                        exit $LASTEXITCODE
                    }

                    & ./jenkins/scripts/prepare-report-email.ps1
                '''

                archiveArtifacts(
                    artifacts: (
                        'runtime/reports/*.html,' +
                        'runtime/reports/*.json,' +
                        'runtime/stages/**/*,' +
                        'runtime/final-validation.json,' +
                        'runtime/pipeline-timeline.json,' +
                        'runtime/build-info.json,' +
                        'runtime/scenario-state.json'
                    ),
                    allowEmptyArchive: false,
                    fingerprint: true
                )

                if (
                    params.REPORT_RECIPIENTS != null &&
                    params.REPORT_RECIPIENTS.trim()
                ) {

                    def emailSubject = powershell(
                        returnStdout: true,
                        script: '''
                            $Meta = Get-Content `
                                ./runtime/reports/email-metadata.json `
                                -Raw |
                                ConvertFrom-Json

                            Write-Output $Meta.subject
                        '''
                    ).trim()

                    def emailBody = readFile(
                        file: 'runtime/reports/ai-canary-email-summary.html'
                    )

                    try {

                        emailext(
                            to: params.REPORT_RECIPIENTS.trim(),
                            subject: emailSubject,
                            mimeType: 'text/html',
                            body: emailBody,
                            attachmentsPattern: (
                                'runtime/reports/' +
                                'ai-canary-deployment-report.html'
                            )
                        )

                        echo(
                            "Detailed report email sent to: " +
                            params.REPORT_RECIPIENTS
                        )
                    }
                    catch (err) {

                        echo(
                            "[WARN] Deployment succeeded, but report email " +
                            "could not be sent: ${err.message}"
                        )
                    }
                }
                else {

                    echo(
                        '[INFO] REPORT_RECIPIENTS is empty. ' +
                        'Report archived in Jenkins; email skipped.'
                    )
                }
            }
        }

        // ----------------------------------------------------
        // FAILURE: ARCHIVE AVAILABLE EVIDENCE + NOTIFY
        // ----------------------------------------------------

        failure {

            script {

                try {

                    archiveArtifacts(
                        artifacts: (
                            'runtime/reports/**/*,' +
                            'runtime/stages/**/*,' +
                            'runtime/*.json'
                        ),
                        allowEmptyArchive: true,
                        fingerprint: false
                    )
                }
                catch (archiveError) {

                    echo(
                        "[WARN] Partial evidence archive failed: " +
                        "${archiveError.message}"
                    )
                }

                if (
                    params.REPORT_RECIPIENTS != null &&
                    params.REPORT_RECIPIENTS.trim()
                ) {

                    try {

                        def failureAttachment = ''

                        if (
                            fileExists(
                                'runtime/reports/' +
                                'ai-canary-deployment-report.html'
                            )
                        ) {
                            failureAttachment = (
                                'runtime/reports/' +
                                'ai-canary-deployment-report.html'
                            )
                        }

                        emailext(
                            to: params.REPORT_RECIPIENTS.trim(),
                            subject: (
                                "AI Canary Deployment Pipeline FAILED | " +
                                "${params.DEMO_SCENARIO} | " +
                                "Build #${env.BUILD_NUMBER}"
                            ),
                            mimeType: 'text/html',
                            body: """
                                <html>
                                <body style="font-family:Segoe UI,Arial,sans-serif;">
                                    <h2>AI Canary Deployment Pipeline</h2>

                                    <p>
                                        Jenkins Build
                                        <strong>#${env.BUILD_NUMBER}</strong>
                                        did not complete successfully.
                                    </p>

                                    <p>
                                        <strong>Scenario:</strong>
                                        ${params.DEMO_SCENARIO}
                                    </p>

                                    <p>
                                        Review the Jenkins console and archived
                                        execution evidence for the failure details.
                                    </p>
                                </body>
                                </html>
                            """,
                            attachmentsPattern: failureAttachment
                        )
                    }
                    catch (mailError) {

                        echo(
                            "[WARN] Failure notification email " +
                            "could not be sent: ${mailError.message}"
                        )
                    }
                }
            }
        }
    }
}
