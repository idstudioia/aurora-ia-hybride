# LA FENÊTRE D'INSTALLATION D'AURORA — 22/11/2026.
# ID Studio IA : « mets-moi une animation pour l'installation sur l'autre PC ». Le modèle est la fenêtre d'installation
# d'Athéna (scripts\fenetre-installation.ps1 de son édition payante, 22/10/2026), dont la méthode est reprise.
#
# Cette fenêtre NE REFAIT PAS l'installation : elle lance installer-aurora.ps1 tel quel, caché, lit ce qu'il écrit
# au fil de l'eau et montre l'étape en cours, à côté de la sphère d'AURORA animée.
#   1. elle lance d'abord l'installateur en mode vérification (-Verifier), qui ne change rien ;
#   2. elle montre ce qui sera téléchargé : depuis le 23/11/2026, aussi Python, Node.js et Ollama quand ils manquent
#      (l'installateur les prend lui-même, « [à prendre] » dans ce qu'il écrit). Rien ne part avant le clic sur
#      « INSTALLER » : c'est l'accord, et l'installateur est alors lancé avec -Oui (et -SansModeles si la case des
#      modèles est décochée, ce qui laisse aussi Ollama de côté) ;
#   3. si un programme n'a pas pu être pris (pas d'Internet, autorisation refusée), elle le dit, avec sa page.
# En secours : « INSTALLER.bat console » garde l'installateur dans sa console, avec ses questions (o/n).
#
#   -Journal <fichier>     où garder ce que l'installateur écrit (sinon dans le dossier temporaire)
#   -Dossier <chemin>      passé à l'installateur (essais : un dossier d'AURORA à part)
#   -SansIcone             passé à l'installateur
#   -Rejouer <journal>     essai : rejoue un journal, sans rien installer ; -CodeRejoue <n> : le code de fin rejoué
#   -Image <fichier.png>   avec -Rejouer : écrit l'image de la fenêtre à la fin du journal, sans l'ouvrir
#   -Analyser <journal>    pour les essais : lit tout le journal et écrit l'état final en JSON, sans fenêtre
#   -Essai <fichier.json>  pour les essais : la vraie fenêtre, ouverte hors de l'écran, qui s'arrête d'elle-même après
#                          la vérification (elle ne clique jamais sur INSTALLER) ou à la fin d'un journal rejoué,
#                          et écrit son état dans ce fichier ; avec -EssaiInstaller, elle « clique » sur INSTALLER
#                          après la vérification (à ne donner qu'avec -Dossier : un dossier d'AURORA à part)
# Le fichier est enregistré en UTF-8 AVEC BOM : sans lui, PowerShell 5.1 lirait mal les accents.
param([string]$Journal = '', [string]$Dossier = '', [switch]$SansIcone, [string]$Rejouer = '', [int]$CodeRejoue = 0,
      [string]$Image = '', [string]$Analyser = '', [string]$Essai = '', [switch]$EssaiInstaller)
$ErrorActionPreference = 'Stop'
$racine = $PSScriptRoot
$installateur = Join-Path $racine 'installer-aurora.ps1'
if (-not $Journal) { $Journal = Join-Path $env:TEMP 'aurora-installation.txt' }
$journalVerification = "$Journal.verification.txt"

# ------------------------------------------------------------------ lecture de ce que l'installateur écrit
$ETAPES = @(
    @{ n = 1; libelle = 'Ce que ce PC a déjà' },
    @{ n = 2; libelle = "L'environnement Python" },
    @{ n = 3; libelle = 'Les bibliothèques Python' },
    @{ n = 4; libelle = "L'interface" },
    @{ n = 5; libelle = "Les modèles d'IA" },
    @{ n = 6; libelle = "L'icône du Bureau" },
    @{ n = 7; libelle = 'Essai du service' }
)

function NouvelEtat {
    $e = @{
        etats = @{}; courante = 0; detail = ''; verification = $false; impossible = $false; termine = $false
        bilan = $false; fin = $null; carte = ''; ollamaAbsent = $false; modeleEnCours = ''; programmeEnCours = ''
        programmes = New-Object System.Collections.ArrayList      # ceux que l'installateur prendra lui-même
        prerequis = New-Object System.Collections.ArrayList; aTelecharger = New-Object System.Collections.ArrayList
        modeles = New-Object System.Collections.ArrayList; remarques = New-Object System.Collections.ArrayList
        dernieres = New-Object System.Collections.ArrayList
    }
    foreach ($x in $ETAPES) { $e.etats[$x.n] = 'attente' }
    return $e
}

function Passer([hashtable]$e, [int]$n) {
    # Ce qui précède devient « fait », sauf ce qui a été signalé (à faire, alerte, échec).
    foreach ($x in $ETAPES) { if ($x.n -lt $n -and $e.etats[$x.n] -in 'attente', 'en cours') { $e.etats[$x.n] = 'fait' } }
    $e.etats[$n] = 'en cours'; $e.courante = $n; $e.detail = ''
}

function LireLigne([hashtable]$e, [string]$brute) {
    $t = ([regex]::Replace($brute, '\x1B\[[0-9;?]*[A-Za-z]', '')).Trim()      # sans les codes de couleur d'Ollama
    if (-not $t) { return }
    if ($t -match 'mode v.rification') { $e.verification = $true; return }
    if ($t -match '^== (\d)\. ') { Passer $e ([int]$Matches[1]); return }
    if ($t -match '^== Bilan') {
        foreach ($x in $ETAPES) { if ($e.etats[$x.n] -eq 'en cours') { $e.etats[$x.n] = 'fait' } }
        $e.bilan = $true; $e.courante = 0; return
    }
    if ($t -match '^Installation impossible') { $e.impossible = $true; if ($e.courante) { $e.etats[$e.courante] = 'echec' }; return }

    # 23/11/2026 : un programme absent, que l'installateur ira chercher lui-même (« [à prendre] Python 3.11.7 (25 Mo,
    # python.org) : … »). Pendant une vérification, c'est une chose à faire, pas un arrêt.
    if ($t -match '^\[. prendre\]\s+(Python|Node\.js|Ollama) (\S+) \((.+?), ([^,)]+)\)') {      # la taille peut avoir une virgule : « 1,5 Go »
        [void]$e.programmes.Add(@{ nom = $Matches[1]; version = $Matches[2]; taille = $Matches[3]; source = $Matches[4] })
        if ($e.courante -and $e.verification) { $e.etats[$e.courante] = 'a faire' }
        $e.detail = "$($Matches[1]) $($Matches[2]) : à télécharger ($($Matches[3]), $($Matches[4]))"
        return
    }
    if ($e.courante -eq 1) {
        # Le téléchargement d'un programme : son nom, puis la barre de curl (« ####   12.3% »), puis l'installation.
        if ($t -match '^(Python|Node\.js|Ollama) (\S+) : t.l.chargement \(') { $e.programmeEnCours = "$($Matches[1]) $($Matches[2])"; $e.detail = "$($e.programmeEnCours) : téléchargement…"; return }
        if ($t -match '^#*\s*(\d{1,3})[.,]\d\s?%$') { if ($e.programmeEnCours) { $e.detail = "$($e.programmeEnCours) : téléchargement, $($Matches[1]) %" }; return }
        if ($t -match '^(Python|Node\.js|Ollama) \S+ : (installation|empreinte officielle|d.j. t.l.charg)' -or $t -match '^Windows demande une autorisation') { $e.detail = $t; return }
    }

    if ($t -match '^\[manque\]\s+(.+)$') {
        $m = $Matches[1]
        if ($m -match '^(Python|Node\.js|Ollama)\b.*?(https?://\S+)') {
            $nom = $Matches[1]; $url = $Matches[2].TrimEnd('.', ',')
            [void]$e.prerequis.Add(@{ nom = $nom; url = $url; bloquant = ($nom -ne 'Ollama') })
            if ($nom -eq 'Ollama') { $e.ollamaAbsent = $true }
        }
        elseif ($e.courante -eq 5 -and $m -match '^(\S+) non t.l.charg. \(([\d.,]+) Go\)') {
            [void]$e.modeles.Add(@{ nom = $Matches[1]; go = [double]($Matches[2].Replace(',', '.')) })
        }
        elseif ($e.courante -eq 3 -and $m -match '^non install') { [void]$e.aTelecharger.Add('bibliotheques') }
        elseif ($e.courante -eq 4 -and $m -match '^non pr.par') { [void]$e.aTelecharger.Add('interface') }
        if ($e.courante) { $e.etats[$e.courante] = if ($e.verification) { 'a faire' } else { 'alerte' } }
        $e.detail = $m; [void]$e.dernieres.Add($m)
        while ($e.dernieres.Count -gt 5) { $e.dernieres.RemoveAt(0) }
        return
    }
    if ($t -match '^\[ok\]\s+(.+)$') {
        $e.detail = $Matches[1]
        if ($e.detail -match '^Carte graphique : (.+)$') { $e.carte = $Matches[1] }
        return
    }

    # pip, npm et Ollama : une ligne lisible à la place de leur défilement.
    if ($e.courante -eq 3) {
        if ($t -match '^(?:Collecting|Downloading) ([A-Za-z0-9_.]+?)(?:[-<>=!~\[ ]|$)') { $e.detail = "Bibliothèques : $($Matches[1])…"; return }
        if ($t -match '^Installing collected packages') { $e.detail = 'Bibliothèques : installation…'; return }
        if ($t -match '^Successfully installed') { $e.detail = 'Bibliothèques installées.'; return }
    }
    if ($e.courante -eq 4 -and $t -match '^added (\d+) packages') { $e.detail = "Interface : $($Matches[1]) paquets installés."; return }
    if ($e.courante -eq 5) {
        if ($t -match '^(\S+) : t.l.chargement \(([\d.,]+) Go\)') { $e.modeleEnCours = $Matches[1]; $e.detail = "$($Matches[1]) : téléchargement ($($Matches[2]) Go)…"; return }
        if ($t -match '^pulling ') {
            $p = [regex]::Matches($t, '(\d{1,3})\s?%')      # le dernier pourcentage de la ligne : le plus récent
            if ($p.Count -and $e.modeleEnCours) { $e.detail = "$($e.modeleEnCours) : téléchargement, $($p[$p.Count - 1].Groups[1].Value) %" }
            return
        }
        if ($t -match '^(verifying|writing manifest|removing|success)') { return }
    }
    if ($t -match '^AURORA est install') { $e.termine = $true; return }
    if ($e.bilan -and $t -match '^- (.+)$') { [void]$e.remarques.Add($Matches[1]); return }
    if ($t -match 'a .chou.|n.a pas pu|s.est arr.t.e|^ERREUR|^ERROR|^Error|npm ERR|^Traceback') {
        [void]$e.dernieres.Add($t)
        while ($e.dernieres.Count -gt 5) { $e.dernieres.RemoveAt(0) }
    }
}

# Découpe un texte en lignes complètes (fin de ligne ou retour chariot seul) ; rend le morceau inachevé à garder.
function LireTexte([hashtable]$e, [string]$texte) {
    $morceaux = [regex]::Split($texte, '\r\n|\r|\n')
    for ($i = 0; $i -lt $morceaux.Length - 1; $i++) { LireLigne $e $morceaux[$i] }
    return $morceaux[$morceaux.Length - 1]
}

function Terminer([hashtable]$e, [int]$code) {
    if ($code -eq 0 -and -not $e.impossible) {
        foreach ($x in $ETAPES) { if ($e.etats[$x.n] -in 'attente', 'en cours') { $e.etats[$x.n] = 'fait' } }
        $e.fin = 'ok'
    } else {
        if ($e.courante -and $e.etats[$e.courante] -eq 'en cours') { $e.etats[$e.courante] = 'echec' }
        $e.fin = 'erreur'
    }
}

# Après une vérification : « manque » (un programme à installer d'abord) ou « pret » (on peut installer).
function ApresVerification([hashtable]$e, [int]$code) {
    if ($e.impossible -or $code -ne 0) { return 'manque' } else { return 'pret' }
}

function EtatEnJson([hashtable]$e, [string]$phase) {
    $etats = [ordered]@{}
    foreach ($x in $ETAPES) { $etats["$($x.n)"] = $e.etats[$x.n] }
    return ([ordered]@{
        phase = $phase; fin = $e.fin; termine = $e.termine; etats = $etats; detail = $e.detail; carte = $e.carte
        programmes = @($e.programmes | ForEach-Object { $_.nom })
        prerequis = @($e.prerequis | ForEach-Object { $_.nom }); aTelecharger = @($e.aTelecharger)
        modeles = @($e.modeles | ForEach-Object { $_.nom }); remarques = @($e.remarques); dernieres = @($e.dernieres)
    } | ConvertTo-Json -Depth 4 -Compress)
}

if ($Analyser) {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
    $e = NouvelEtat
    $reste = LireTexte $e ([IO.File]::ReadAllText($Analyser, [Text.Encoding]::UTF8))
    if ($reste) { LireLigne $e $reste }
    $phase = if ($e.verification) { ApresVerification $e $CodeRejoue } else { Terminer $e $CodeRejoue; 'fin' }
    EtatEnJson $e $phase
    exit 0
}

# ------------------------------------------------------------------ la fenêtre
$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Installation d'AURORA" Width="680" Height="400" WindowStyle="None" ResizeMode="NoResize"
        WindowStartupLocation="CenterScreen" Background="#050816" FontFamily="Segoe UI" Foreground="#E8F0FF">
  <Window.Resources>
    <Style TargetType="Button">
      <Setter Property="Foreground" Value="#E8F0FF"/>
      <Setter Property="FontSize" Value="11.5"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Padding" Value="13,7"/>
      <Setter Property="Margin" Value="0,0,8,0"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Fond" Background="#142A66" BorderBrush="#36D6FF" BorderThickness="1" CornerRadius="4" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="Fond" Property="Background" Value="#1F3F99"/></Trigger>
              <Trigger Property="IsEnabled" Value="False"><Setter TargetName="Fond" Property="Opacity" Value="0.4"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>
  <Window.Triggers>
    <EventTrigger RoutedEvent="Window.Loaded">
      <BeginStoryboard>
        <Storyboard>
          <DoubleAnimation Storyboard.TargetName="Tour1" Storyboard.TargetProperty="(UIElement.RenderTransform).(RotateTransform.Angle)" From="0" To="360" Duration="0:0:5" RepeatBehavior="Forever"/>
          <DoubleAnimation Storyboard.TargetName="Tour2" Storyboard.TargetProperty="(UIElement.RenderTransform).(RotateTransform.Angle)" From="360" To="0" Duration="0:0:8" RepeatBehavior="Forever"/>
          <DoubleAnimation Storyboard.TargetName="Halo" Storyboard.TargetProperty="Opacity" From="0.45" To="1" Duration="0:0:1.8" AutoReverse="True" RepeatBehavior="Forever"/>
          <DoubleAnimation Storyboard.TargetName="Halo" Storyboard.TargetProperty="(UIElement.RenderTransform).(ScaleTransform.ScaleX)" From="0.93" To="1.05" Duration="0:0:1.8" AutoReverse="True" RepeatBehavior="Forever"/>
          <DoubleAnimation Storyboard.TargetName="Halo" Storyboard.TargetProperty="(UIElement.RenderTransform).(ScaleTransform.ScaleY)" From="0.93" To="1.05" Duration="0:0:1.8" AutoReverse="True" RepeatBehavior="Forever"/>
        </Storyboard>
      </BeginStoryboard>
    </EventTrigger>
  </Window.Triggers>
  <Border BorderBrush="#2A3F8F" BorderThickness="1">
    <Border.Background>
      <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
        <GradientStop Color="#050816" Offset="0"/>
        <GradientStop Color="#0C1436" Offset="1"/>
      </LinearGradientBrush>
    </Border.Background>
    <Grid>
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="262"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <Grid Grid.Column="0" Width="236" Height="236" HorizontalAlignment="Center" VerticalAlignment="Center">
        <Ellipse x:Name="Halo" Width="236" Height="236" RenderTransformOrigin="0.5,0.5" Opacity="0.8">
          <Ellipse.Fill>
            <RadialGradientBrush>
              <GradientStop x:Name="HaloCoeur" Color="#E63F7BFF" Offset="0.66"/>
              <GradientStop Color="#003F7BFF" Offset="1"/>
            </RadialGradientBrush>
          </Ellipse.Fill>
          <Ellipse.RenderTransform><ScaleTransform ScaleX="1" ScaleY="1"/></Ellipse.RenderTransform>
        </Ellipse>
        <Image x:Name="Sphere" Width="172" Height="172"/>
        <Grid Width="232" Height="232" RenderTransformOrigin="0.5,0.5">
          <Grid.RenderTransform>
            <TransformGroup><ScaleTransform ScaleY="0.30"/><RotateTransform Angle="-16"/></TransformGroup>
          </Grid.RenderTransform>
          <Ellipse Stroke="#6636D6FF" StrokeThickness="1.4"/>
          <Grid x:Name="Tour1" RenderTransformOrigin="0.5,0.5">
            <Grid.RenderTransform><RotateTransform Angle="40"/></Grid.RenderTransform>
            <Ellipse Width="11" Height="11" Fill="#FFFFFF" VerticalAlignment="Top" HorizontalAlignment="Center" Margin="0,-5,0,0">
              <Ellipse.Effect><DropShadowEffect Color="#36D6FF" BlurRadius="16" ShadowDepth="0"/></Ellipse.Effect>
            </Ellipse>
          </Grid>
        </Grid>
        <Grid Width="232" Height="232" RenderTransformOrigin="0.5,0.5">
          <Grid.RenderTransform>
            <TransformGroup><ScaleTransform ScaleY="0.22"/><RotateTransform Angle="22"/></TransformGroup>
          </Grid.RenderTransform>
          <Ellipse Stroke="#66A56BFF" StrokeThickness="1.2"/>
          <Grid x:Name="Tour2" RenderTransformOrigin="0.5,0.5">
            <Grid.RenderTransform><RotateTransform Angle="200"/></Grid.RenderTransform>
            <Ellipse Width="9" Height="9" Fill="#FFFFFF" VerticalAlignment="Top" HorizontalAlignment="Center" Margin="0,-4,0,0">
              <Ellipse.Effect><DropShadowEffect Color="#A56BFF" BlurRadius="16" ShadowDepth="0"/></Ellipse.Effect>
            </Ellipse>
          </Grid>
        </Grid>
      </Grid>
      <Grid Grid.Column="1" Margin="4,20,24,20">
        <Grid.RowDefinitions>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="*"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>
        <TextBlock Grid.Row="0" Text="AURORA  ·  ASSISTANT IA LOCAL" FontSize="10.5" Foreground="#36D6FF" FontWeight="SemiBold"/>
        <TextBlock x:Name="Croix" Grid.Row="0" Text="✕" FontSize="13" Foreground="#8FA3CF" HorizontalAlignment="Right" Cursor="Hand" Margin="0,-4,-8,0" Padding="6,2"/>
        <TextBlock x:Name="Titre" Grid.Row="1" Text="Installation d'AURORA" FontSize="21" FontWeight="SemiBold" Margin="0,6,0,10" TextWrapping="Wrap"/>
        <TextBlock x:Name="Etapes" Grid.Row="2" FontSize="13" LineHeight="21" TextWrapping="Wrap" VerticalAlignment="Top"/>
        <TextBlock x:Name="Detail" Grid.Row="3" FontSize="11.5" Foreground="#8FA3CF" TextWrapping="Wrap" Margin="0,4,0,8" MaxHeight="48"/>
        <Grid Grid.Row="4" Margin="0,0,0,10">
          <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
          <ProgressBar x:Name="Barre" Height="4" IsIndeterminate="True" Foreground="#36D6FF" Background="#16214D" BorderThickness="0" VerticalAlignment="Center"/>
          <TextBlock x:Name="Temps" Grid.Column="1" FontSize="11" Foreground="#8FA3CF" Margin="12,0,0,0" MinWidth="58" TextAlignment="Right"/>
        </Grid>
        <CheckBox x:Name="CaseModeles" Grid.Row="5" IsChecked="True" Foreground="#E8F0FF" FontSize="12" Margin="0,0,0,10" Visibility="Collapsed"/>
        <WrapPanel Grid.Row="6">
          <Button x:Name="BInstaller" Content="INSTALLER" Visibility="Collapsed"/>
          <Button x:Name="BPages" Content="OUVRIR LES PAGES" Visibility="Collapsed"/>
          <Button x:Name="BReverifier" Content="REVÉRIFIER" Visibility="Collapsed"/>
          <Button x:Name="BLancer" Content="LANCER AURORA" Visibility="Collapsed"/>
          <Button x:Name="BReessayer" Content="RÉESSAYER" Visibility="Collapsed"/>
          <Button x:Name="BJournal" Content="VOIR LE JOURNAL" Visibility="Collapsed"/>
          <Button x:Name="BFermer" Content="FERMER" Visibility="Collapsed"/>
        </WrapPanel>
      </Grid>
    </Grid>
  </Border>
</Window>
'@

try {
    Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
    $fenetre = [Windows.Markup.XamlReader]::Parse($xaml)
} catch {
    # La fenêtre n'a pas pu se construire : l'installation continue dans la console, plutôt que rien.
    if (-not $Rejouer) { Start-Process -FilePath $env:ComSpec -ArgumentList '/k', ('""' + (Join-Path $racine 'INSTALLER.bat') + '" console"') -WorkingDirectory $racine }
    exit 1
}

# « $zoneEtapes », pas « $etapes » : PowerShell ne distingue pas les majuscules, et ce nom écraserait la liste $ETAPES.
$titre = $fenetre.FindName('Titre'); $zoneEtapes = $fenetre.FindName('Etapes'); $detail = $fenetre.FindName('Detail')
$barre = $fenetre.FindName('Barre'); $temps = $fenetre.FindName('Temps'); $caseModeles = $fenetre.FindName('CaseModeles')
$halo = $fenetre.FindName('HaloCoeur')
$bInstaller = $fenetre.FindName('BInstaller'); $bPages = $fenetre.FindName('BPages'); $bReverifier = $fenetre.FindName('BReverifier')
$bLancer = $fenetre.FindName('BLancer'); $bReessayer = $fenetre.FindName('BReessayer'); $bJournal = $fenetre.FindName('BJournal')
$bFermer = $fenetre.FindName('BFermer')
$pinceaux = New-Object Windows.Media.BrushConverter
$COULEUR = @{ fait = '#36D6FF'; 'en cours' = '#FFFFFF'; echec = '#FF6B6B'; alerte = '#FFC857'; 'a faire' = '#FFC857'; attente = '#5E6B8C'; texte = '#E8F0FF' }
$MARQUE = @{ fait = '■'; 'en cours' = '►'; echec = '×'; alerte = '!'; 'a faire' = '○'; attente = '□' }

$png = Join-Path $racine 'assets\aurora-sphere.png'
if (Test-Path -LiteralPath $png) {
    $bitmap = New-Object Windows.Media.Imaging.BitmapImage
    $bitmap.BeginInit(); $bitmap.CacheOption = 'OnLoad'; $bitmap.UriSource = New-Object Uri($png); $bitmap.DecodePixelWidth = 344; $bitmap.EndInit()
    $fenetre.FindName('Sphere').Source = $bitmap
}
$ico = Join-Path $racine 'assets\aurora.ico'
if (Test-Path -LiteralPath $ico) { try { $fenetre.Icon = [Windows.Media.Imaging.BitmapFrame]::Create((New-Object Uri($ico))) } catch { } }

$script:phase = 'verification'      # verification | manque | pret | installation | fin
$script:e = NouvelEtat
$script:run = @{ proc = $null; journal = ''; position = 0; reste = ''; decodeur = [Text.Encoding]::UTF8.GetDecoder(); debut = Get-Date; texte = $null; avance = 0 }

function Duree([int]$s) { if ($s -lt 60) { return "$s s" } else { return ('{0} min {1:00} s' -f [int][math]::Floor($s / 60), ($s % 60)) } }

function EcrireLignes([object[]]$lignes) {
    # Chaque ligne : un texte, ou @{ texte; couleur; gras }.
    $zoneEtapes.Inlines.Clear()
    foreach ($l in $lignes) {
        $ligne = if ($l -is [hashtable]) { $l } else { @{ texte = [string]$l } }
        $morceau = New-Object Windows.Documents.Run ([string]$ligne.texte)
        $morceau.Foreground = $pinceaux.ConvertFromString($(if ($ligne.couleur) { $ligne.couleur } else { $COULEUR.texte }))
        if ($ligne.gras) { $morceau.FontWeight = 'SemiBold' }
        $zoneEtapes.Inlines.Add($morceau); $zoneEtapes.Inlines.Add((New-Object Windows.Documents.LineBreak))
    }
}

function LignesDesEtapes {
    foreach ($x in $ETAPES) {
        $etat = $e.etats[$x.n]
        @{ texte = "$($MARQUE[$etat])  $($x.libelle)"; couleur = $COULEUR[$etat]; gras = ($etat -eq 'en cours') }
    }
}

function Afficher {
    foreach ($b in $bInstaller, $bPages, $bReverifier, $bLancer, $bReessayer, $bJournal, $bFermer) { $b.Visibility = 'Collapsed' }
    $caseModeles.Visibility = 'Collapsed'
    $barre.Visibility = if ($phase -in 'verification', 'installation') { 'Visible' } else { 'Hidden' }
    # Le halo passe au rouge quand l'installation s'arrête sur une erreur.
    try { $halo.Color = [Windows.Media.ColorConverter]::ConvertFromString($(if ($phase -eq 'fin' -and $e.fin -eq 'erreur') { '#E6FF4D4D' } else { '#E63F7BFF' })) } catch { }
    if ($phase -in 'manque', 'pret') { $temps.Text = '' }

    if ($phase -eq 'verification') {
        $titre.Text = 'Vérification de ce PC…'
        EcrireLignes @(LignesDesEtapes)
        $detail.Text = if ($e.detail) { $e.detail } else { 'Rien n''est changé pendant cette vérification.' }
    }
    elseif ($phase -eq 'manque') {
        $titre.Text = 'Il manque un programme'
        $lignes = @(foreach ($p in $e.prerequis) {
            @{ texte = "×  $($p.nom)  —  $($p.url -replace '^https?://', '')"; couleur = $(if ($p.bloquant) { $COULEUR.echec } else { $COULEUR.alerte }) }
        })
        if (-not $lignes.Count) { $lignes = @(@{ texte = '×  La vérification s''est arrêtée.'; couleur = $COULEUR.echec }) + @($e.dernieres | Select-Object -Last 3) }
        EcrireLignes ($lignes + @('', 'Installez ce qui manque depuis sa page, puis cliquez sur REVÉRIFIER.', 'Python : une version de 3.11 à 3.14, en 64 bits.'))
        $detail.Text = 'Rien n''a été changé sur ce PC.'
        $bPages.Visibility = if ($e.prerequis.Count) { 'Visible' } else { 'Collapsed' }
        $bReverifier.Visibility = 'Visible'; $bJournal.Visibility = 'Visible'; $bFermer.Visibility = 'Visible'
    }
    elseif ($phase -eq 'pret') {
        $titre.Text = 'Ce PC est prêt'
        $lignes = New-Object System.Collections.ArrayList
        # 23/11/2026 : les programmes que l'installateur prendra lui-même (Python, Node.js) viennent en premier ; Ollama
        # va avec les modèles, sous la même case. Une ligne par sorte, pour que tout tienne dans la fenêtre.
        $ollamaAPrendre = @($e.programmes | Where-Object { $_.nom -eq 'Ollama' }) | Select-Object -First 1
        $programmes = @($e.programmes | Where-Object { $_.nom -ne 'Ollama' })
        if ($programmes.Count -or $ollamaAPrendre -or $e.aTelecharger.Count -or $e.modeles.Count) {
            [void]$lignes.Add('À télécharger, chez leurs éditeurs officiels :')
            foreach ($p in $programmes) { [void]$lignes.Add(@{ texte = "○  $($p.nom) $($p.version) ($($p.taille), $($p.source))"; couleur = $COULEUR.alerte }) }
            if ($e.aTelecharger -contains 'bibliotheques') { [void]$lignes.Add(@{ texte = '○  Les bibliothèques Python (environ 350 Mo)'; couleur = $COULEUR.alerte }) }
            if ($e.aTelecharger -contains 'interface') { [void]$lignes.Add(@{ texte = '○  Les bibliothèques de l''interface (environ 140 Mo)'; couleur = $COULEUR.alerte }) }
            if ($ollamaAPrendre) { [void]$lignes.Add(@{ texte = "○  Ollama $($ollamaAPrendre.version) ($($ollamaAPrendre.taille), $($ollamaAPrendre.source))"; couleur = $COULEUR.alerte }) }
            if ($e.modeles.Count) {
                $go = ([string][math]::Round(($e.modeles | ForEach-Object { $_.go } | Measure-Object -Sum).Sum, 1)).Replace('.', ',')
                [void]$lignes.Add(@{ texte = "○  $(if ($e.modeles.Count -gt 1) { 'Les modèles' } else { 'Le modèle' }) $(@($e.modeles | ForEach-Object { $_.nom }) -join ', ') ($go Go)"; couleur = $COULEUR.alerte })
            }
        } else {
            [void]$lignes.Add('Tout est déjà téléchargé sur ce PC.')
        }
        if ($e.ollamaAbsent) { [void]$lignes.Add(@{ texte = '!  Ollama manque : AURORA s''installera sans modèle local.'; couleur = $COULEUR.alerte }) }
        EcrireLignes $lignes.ToArray()
        $detail.Text = $(if ($e.carte) { "Carte graphique : $($e.carte). " } else { '' }) + 'Rien n''est téléchargé avant le clic sur INSTALLER.' + $(if ($programmes | Where-Object { $_.nom -eq 'Node.js' }) { ' Windows demandera une autorisation pour Node.js.' } else { '' })
        if ($e.modeles.Count -or $ollamaAPrendre) {
            $total = [double](($e.modeles | ForEach-Object { $_.go } | Measure-Object -Sum).Sum) + $(if ($ollamaAPrendre) { 1.5 } else { 0 })
            $caseModeles.Content = "Télécharger aussi $(if ($ollamaAPrendre) { 'Ollama et ' } else { '' })les modèles d'IA ($(([string][math]::Round($total, 1)).Replace('.', ',')) Go)"
            $caseModeles.Visibility = 'Visible'
        }
        $bInstaller.Visibility = 'Visible'; $bFermer.Visibility = 'Visible'
    }
    elseif ($phase -eq 'installation') {
        $titre.Text = 'Installation d''AURORA…'
        EcrireLignes @(LignesDesEtapes)
        $detail.Text = $e.detail
    }
    else {
        $bJournal.Visibility = 'Visible'; $bFermer.Visibility = 'Visible'
        if ($e.fin -eq 'ok') {
            $titre.Text = 'AURORA est installée'
            $lignes = @(LignesDesEtapes)
            EcrireLignes $lignes
            $detail.Text = if ($e.remarques.Count) { 'Il reste : ' + (@($e.remarques) -join ' · ') } else { 'Double-cliquez sur l''icône « AURORA » du Bureau, ou sur LANCER AURORA.' }
            $bLancer.Visibility = if ($Rejouer) { 'Collapsed' } else { 'Visible' }
        } else {
            $titre.Text = 'L''installation s''est arrêtée'
            $lignes = @(LignesDesEtapes)
            EcrireLignes $lignes
            $detail.Text = if ($e.dernieres.Count) { (@($e.dernieres | Select-Object -Last 2) -join ' · ') } else { 'Voir le journal pour le détail.' }
            $bReessayer.Visibility = if ($Rejouer) { 'Collapsed' } else { 'Visible' }
            # Un programme que l'installateur n'a pas pu prendre lui-même : sa page, pour l'installer à la main.
            $bPages.Visibility = if ($e.prerequis.Count -and -not $Rejouer) { 'Visible' } else { 'Collapsed' }
        }
    }
}

function Lancer([string]$arguments, [string]$journal) {
    if (Test-Path -LiteralPath $journal) { Remove-Item -LiteralPath $journal -Force }
    $dossierJournal = Split-Path -Parent $journal
    if ($dossierJournal -and -not (Test-Path -LiteralPath $dossierJournal)) { New-Item -ItemType Directory -Force -Path $dossierJournal | Out-Null }
    # cmd /d /c ""powershell.exe" … > "journal" 2>&1" : les guillemets extérieurs sont retirés par cmd. Les chemins
    # passent par des variables d'environnement, jamais recopiés dans la commande (leçon d'Athéna : une apostrophe
    # dans un nom de dossier coupait la commande).
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = '/d /c ""powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%AURORA_FI_SCRIPT%" %AURORA_FI_ARGS% > "%AURORA_FI_JOURNAL%" 2>&1"'
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true; $psi.WorkingDirectory = $racine
    $psi.EnvironmentVariables['AURORA_FI_SCRIPT'] = $installateur
    $psi.EnvironmentVariables['AURORA_FI_ARGS'] = $arguments
    $psi.EnvironmentVariables['AURORA_FI_JOURNAL'] = $journal
    $psi.EnvironmentVariables['AURORA_FENETRE'] = '1'
    # Python ou Node.js viennent peut-être d'être installés : relire le PATH de Windows, que cette fenêtre a reçu avant.
    $chemin = (@([Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User')) | Where-Object { $_ }) -join ';'
    if ($chemin) { $psi.EnvironmentVariables['Path'] = $chemin }
    return [System.Diagnostics.Process]::Start($psi)
}

function Demarrer([string]$quoi) {
    $script:phase = $quoi; $script:e = NouvelEtat
    $run.position = 0; $run.reste = ''; $run.decodeur = [Text.Encoding]::UTF8.GetDecoder(); $run.debut = Get-Date; $run.avance = 0
    if ($Rejouer) {
        $run.texte = [IO.File]::ReadAllText($Rejouer, [Text.Encoding]::UTF8)
        $script:phase = if ($run.texte -match 'mode v.rification') { 'verification' } else { 'installation' }
    } else {
        $arguments = if ($quoi -eq 'verification') { '-Verifier' } else { '-Oui' + $(if ($caseModeles.IsChecked -eq $false) { ' -SansModeles' } else { '' }) }
        if ($Dossier) { $arguments += ' -Dossier "' + $Dossier + '"' }
        if ($SansIcone) { $arguments += ' -SansIcone' }
        $run.journal = if ($quoi -eq 'verification') { $journalVerification } else { $Journal }
        $run.proc = Lancer $arguments $run.journal
    }
    $minuterie.Start()
    Afficher
}

function LireNouveau {
    if ($Rejouer) {
        $fin = [math]::Min($run.texte.Length, $run.avance + 220)
        $morceau = $run.texte.Substring($run.avance, $fin - $run.avance); $run.avance = $fin
        return $morceau
    }
    if (-not (Test-Path -LiteralPath $run.journal)) { return '' }
    try {
        $fs = New-Object IO.FileStream($run.journal, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        try {
            if ($fs.Length -le $run.position) { return '' }
            [void]$fs.Seek($run.position, [IO.SeekOrigin]::Begin)
            $octets = New-Object byte[] ($fs.Length - $run.position)
            $n = $fs.Read($octets, 0, $octets.Length); $run.position += $n
            $car = New-Object char[] ($run.decodeur.GetCharCount($octets, 0, $n))
            [void]$run.decodeur.GetChars($octets, 0, $n, $car, 0)
            return -join $car
        } finally { $fs.Dispose() }
    } catch { return '' }
}

function Avancer {
    $nouveau = LireNouveau
    if ($nouveau) { $run.reste = LireTexte $e ($run.reste + $nouveau) }
    $finie = if ($Rejouer) { $run.avance -ge $run.texte.Length } else { $run.proc.HasExited }
    if ($finie -and $phase -in 'verification', 'installation') {
        $nouveau = LireNouveau
        if ($nouveau) { $run.reste = LireTexte $e ($run.reste + $nouveau) }
        if ($run.reste) { LireLigne $e $run.reste; $run.reste = '' }
        $code = if ($Rejouer) { $CodeRejoue } else { $run.proc.ExitCode }
        $minuterie.Stop()
        if ($phase -eq 'verification') { $script:phase = ApresVerification $e $code } else { Terminer $e $code; $script:phase = 'fin' }
    }
    $temps.Text = Duree ([int]((Get-Date) - $run.debut).TotalSeconds)
    Afficher
    if ($Essai -and $EssaiInstaller -and $Dossier -and $phase -eq 'pret' -and -not $run.installe) {
        $run.installe = $true; Demarrer 'installation'; return      # l'essai « clique » sur INSTALLER, une seule fois
    }
    if ($Essai -and $phase -in 'manque', 'pret', 'fin') {
        [IO.File]::WriteAllText($Essai, (EtatEnJson $e $phase), (New-Object Text.UTF8Encoding $false))
        $fenetre.Close()
    }
}

$minuterie = New-Object Windows.Threading.DispatcherTimer
$minuterie.Interval = [TimeSpan]::FromMilliseconds(250)
$minuterie.Add_Tick({ Avancer })

if ($Image) {
    # L'image de la fenêtre à la fin du journal rejoué, sans l'ouvrir : pour la regarder avant de la montrer.
    $script:run.texte = [IO.File]::ReadAllText($Rejouer, [Text.Encoding]::UTF8)
    $script:phase = if ($run.texte -match 'mode v.rification') { 'verification' } else { 'installation' }
    $reste = LireTexte $e $run.texte
    if ($reste) { LireLigne $e $reste }
    if ($CodeRejoue -ge 0) { if ($phase -eq 'verification') { $script:phase = ApresVerification $e $CodeRejoue } else { Terminer $e $CodeRejoue; $script:phase = 'fin' } }
    $temps.Text = Duree 119
    Afficher
    $contenu = $fenetre.Content
    $contenu.Measure((New-Object Windows.Size 680, 400)); $contenu.Arrange((New-Object Windows.Rect 0, 0, 680, 400)); $contenu.UpdateLayout()
    $rendu = New-Object Windows.Media.Imaging.RenderTargetBitmap 1360, 800, 192, 192, ([Windows.Media.PixelFormats]::Pbgra32)
    $rendu.Render($contenu)
    $codeur = New-Object Windows.Media.Imaging.PngBitmapEncoder
    $codeur.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($rendu))
    $sortie = [IO.File]::Create($Image)
    try { $codeur.Save($sortie) } finally { $sortie.Close() }
    exit 0
}

if ($Essai) {
    # Essai : la vraie fenêtre, mais hors de l'écran et sans prendre la main à ce qui est ouvert.
    $fenetre.WindowStartupLocation = 'Manual'; $fenetre.Left = -20000; $fenetre.Top = -20000
    $fenetre.ShowInTaskbar = $false; $fenetre.ShowActivated = $false
}
$fenetre.Add_MouseLeftButtonDown({ try { $fenetre.DragMove() } catch { } })
$fenetre.FindName('Croix').Add_MouseLeftButtonUp({ $fenetre.Close() })
$bFermer.Add_Click({ $fenetre.Close() })
$bInstaller.Add_Click({ Demarrer 'installation' })
$bReverifier.Add_Click({ Demarrer 'verification' })
$bReessayer.Add_Click({ Demarrer 'verification' })
$bPages.Add_Click({ foreach ($p in $e.prerequis) { Start-Process $p.url } })
$bJournal.Add_Click({
    $j = if ($Rejouer) { $Rejouer } elseif (Test-Path -LiteralPath $Journal) { $Journal } else { $journalVerification }
    Start-Process notepad.exe -ArgumentList ('"' + $j + '"')
})
$bLancer.Add_Click({
    Start-Process -FilePath (Join-Path $racine 'AURORA.bat') -WorkingDirectory $racine -WindowStyle Minimized
    $fenetre.Close()
})
$fenetre.Add_Closing({
    param($source, $arguments)
    if ($run.proc -and -not $run.proc.HasExited -and $phase -eq 'installation') {
        $reponse = [Windows.MessageBox]::Show("L'installation n'est pas finie. L'arrêter ?`r`n`r`nCe qui est déjà téléchargé est gardé : relancez INSTALLER.bat pour reprendre.", 'AURORA', 'YesNo', 'Warning')
        if ($reponse -ne 'Yes') { $arguments.Cancel = $true; return }
    }
    if ($run.proc -and -not $run.proc.HasExited) { & taskkill.exe /T /F /PID $run.proc.Id | Out-Null }
    $minuterie.Stop()
})
$fenetre.Add_ContentRendered({ Demarrer 'verification' })

try {
    [void]$fenetre.ShowDialog()
} catch {
    if (-not $Rejouer -and -not $run.proc) { Start-Process -FilePath $env:ComSpec -ArgumentList '/k', ('""' + (Join-Path $racine 'INSTALLER.bat') + '" console"') -WorkingDirectory $racine }
}
