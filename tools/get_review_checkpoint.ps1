#requires -Version 7.0
param(
 [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceCommit,
 [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$Repository='Personal-Stuffs-johnmart19/jarvis',
 [ValidateRange(1,2147483647)][int]$PullRequest=7,
 [string]$CheckoutPath=(Split-Path $PSScriptRoot),
 [ValidatePattern('^$|^[0-9a-f]{40}$')][string]$ArtifactCommit='',
 [string]$EvidencePath=''
)
$ErrorActionPreference='Stop'
$result=[ordered]@{sourceCommit=$SourceCommit;prHead=$null;reviewCoverage='unconfirmed';approval='not_approved';checkpointState='pending';evidence=$null;artifact=$null;checks=@();findingsStillRequireTriage=$true;scope='Coverage is not approval, bundled QA or publication.';nextStep='Record checkpoint and end the wait; do not poll unchanged source in a loop.'}
function Read-GitHub([string[]]$Arguments){
 $info=[Diagnostics.ProcessStartInfo]::new((Get-Command gh -ErrorAction Stop).Source)
 $info.UseShellExecute=$false;$info.CreateNoWindow=$true
 $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
 foreach($argument in $Arguments){$info.ArgumentList.Add($argument)}
 $process=[Diagnostics.Process]::new();$process.StartInfo=$info
 try{
  if(!$process.Start()){throw 'Cannot start read-only GitHub request.'}
  $output=$process.StandardOutput.ReadToEndAsync();$errors=$process.StandardError.ReadToEndAsync()
  if(!$process.WaitForExit(30000)){
   # This process was created by this function; no user/worker process is targeted.
   $process.Kill($true);$process.WaitForExit()
   throw 'GitHub read exceeded 30 seconds; coverage is unknown.'
  }
  if($process.ExitCode -ne 0){throw 'GitHub read failed; coverage is unknown.'}
  return ($output.GetAwaiter().GetResult()|ConvertFrom-Json)
 }finally{$process.Dispose()}
}
function Read-Delta([string]$Base,[string]$Target){
 foreach($revision in @($Base,$Target)){
  & git -C $CheckoutPath cat-file -e "$revision^{commit}" 2>$null
  if($LASTEXITCODE -ne 0){throw "Commit unavailable locally: $revision. No fetch attempted."}
 }
 & git -C $CheckoutPath merge-base --is-ancestor $Base $Target
 if($LASTEXITCODE -ne 0){throw 'Source is not an ancestor of compared head.'}
 $paths=@(& git -C $CheckoutPath diff --name-only $Base $Target)
 if($LASTEXITCODE -ne 0){throw 'Cannot inspect commit difference.'}
 return [pscustomobject]@{paths=$paths;sourceChanges=@($paths|Where-Object{$_ -notmatch '^(docs/|desktop-bin/)'})}
}
try{
 if($EvidencePath){$snapshot=Get-Content -LiteralPath $EvidencePath -Raw|ConvertFrom-Json}
 else{
  # Three bounded read-only commands; never request review or watch in a loop.
  $head=Read-GitHub @('pr','view',"$PullRequest",'--repo',$Repository,'--json','headRefOid,statusCheckRollup')
  $comments=Read-GitHub @('api',"repos/$Repository/issues/$PullRequest/comments?per_page=100",'--paginate','--slurp')
  $reviews=Read-GitHub @('api',"repos/$Repository/pulls/$PullRequest/reviews?per_page=100",'--paginate','--slurp')
  $snapshot=[pscustomobject]@{head=$head.headRefOid;checks=@($head.statusCheckRollup);comments=@($comments|ForEach-Object{$_});reviews=@($reviews|ForEach-Object{$_})}
 }
 if($snapshot.head -notmatch '^[0-9a-f]{40}$' -or $null -eq $snapshot.comments -or $null -eq $snapshot.reviews -or $null -eq $snapshot.checks){throw 'Incomplete or invalid snapshot.'}
 $result.prHead=$snapshot.head
 $bots=@('coderabbiteu[bot]','coderabbitai[bot]')
 $reviews=@($snapshot.reviews|Where-Object{$_.user.login -in $bots -and $_.commit_id -eq $SourceCommit}|Sort-Object submitted_at -Descending)
 if($reviews.Count -gt 0){
  $latest=$reviews[0]
  if($latest.state -eq 'APPROVED'){$result.approval='approved'}
  elseif($latest.state -eq 'CHANGES_REQUESTED'){$result.approval='changes_requested'}
  if($latest.state -in @('APPROVED','COMMENTED','CHANGES_REQUESTED')){$result.reviewCoverage='reviewed';$result.evidence=@{source='PR review';url=$latest.html_url;state=$latest.state}}
 }
 $found=$false
 foreach($comment in @($snapshot.comments|Where-Object{$_.user.login -in $bots}|Sort-Object updated_at -Descending)){
  foreach($match in [regex]::Matches($comment.body,'<!--\s*final_review_risk_coverage:(\{[^\r\n]*?\})\s*-->')){
   $coverage=$match.Groups[1].Value|ConvertFrom-Json
   if($coverage.sourceCommitId -ne $SourceCommit){continue}
   $found=$true
   if($coverage.coveredCommitId -eq $SourceCommit -and $coverage.kind -eq 'reviewed'){
    $result.reviewCoverage='reviewed';$result.evidence=@{source='CodeRabbit summary';url=$comment.html_url;updated=$comment.updated_at}
   }else{$result.reviewCoverage='unconfirmed';$result.evidence=@{source='CodeRabbit summary';kind=$coverage.kind;coveredCommit=$coverage.coveredCommitId}}
   break
  }
  if($found){break}
 }
 if($reviews.Count -gt 0 -and $reviews[0].state -in @('DISMISSED','PENDING')){
  $result.reviewCoverage='unconfirmed';$result.approval='not_approved'
 }
 if($result.reviewCoverage -ne 'reviewed'){$result.approval='not_approved'}
 if($snapshot.head -ne $SourceCommit){
  $delta=Read-Delta $SourceCommit $snapshot.head
  if($delta.sourceChanges.Count -gt 0){$result.reviewCoverage='stale';$result.approval='not_approved';$result.nextStep='Source changed; inspect only the changed source and its evidence.'}
 }
 if($ArtifactCommit){
  $delta=Read-Delta $SourceCommit $ArtifactCommit
  $result.artifact=@{commit=$ArtifactCommit;diffInspected=$true;changedPaths=$delta.paths;relation=$(if($delta.sourceChanges.Count -eq 0){'only_docs_or_artifact_paths_changed'}else{'contains_source_changes'});reviewInherited=$false;binaryProvenanceVerified=$false}
 }
 $result.checks=@($snapshot.checks|ForEach-Object{@{name=$(if($_.name){$_.name}else{$_.context});status=$_.status;state=$_.state;conclusion=$_.conclusion}})
 $failed=@($result.checks|Where-Object{$_.state -in @('FAILURE','ERROR') -or $_.conclusion -in @('FAILURE','CANCELLED','TIMED_OUT','ACTION_REQUIRED','STARTUP_FAILURE','STALE')})
 $pending=@($result.checks|Where-Object{$_.state -in @('PENDING','EXPECTED') -or $_.status -in @('QUEUED','IN_PROGRESS','WAITING','PENDING','REQUESTED')})
 if($failed.Count -gt 0){$result.checkpointState='blocked_checks'}
 elseif($result.reviewCoverage -eq 'stale'){$result.checkpointState='stale'}
 elseif($result.approval -eq 'changes_requested'){$result.checkpointState='changes_requested'}
 elseif($pending.Count -gt 0){$result.checkpointState='pending_checks'}
 elseif($result.reviewCoverage -eq 'reviewed'){$result.checkpointState='reviewed';$result.nextStep='Consume findings once; reuse separate QA/artifact evidence. Do not wait for a duplicate review record.'}
}catch{$result.reviewCoverage='unknown';$result.approval='not_approved';$result.checkpointState='error';$result.error=$_.Exception.Message}
$result|ConvertTo-Json -Depth 8
if($result.checkpointState -eq 'reviewed'){exit 0}
if($result.checkpointState -eq 'error'){exit 1}
exit 2
