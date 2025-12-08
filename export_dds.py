from qgis.PyQt.QtCore import QCoreApplication, QSize
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterExtent,
    QgsProcessingParameterBoolean,
    QgsProject,
    QgsMapSettings,
    QgsMapRendererParallelJob,
    QgsRectangle,
    QgsSettings
)
import os
import subprocess
import tempfile
import shutil
from qgis.utils import iface

class ExportDDSCustomMips(QgsProcessingAlgorithm):
    # --- パラメータID ---
    P_EXTENT = 'EXTENT'             
    P_USE_CUSTOM = 'USE_CUSTOM'     
    P_SIZE_ENUM = 'SIZE_ENUM'       
    P_WIDTH = 'WIDTH'               
    P_HEIGHT = 'HEIGHT'             
    P_FORMAT = 'FORMAT'             
    P_MAX_LEVELS = 'MAX_LEVELS'     
    P_TEX_ASSEMBLE = 'TEX_ASSEMBLE'
    P_TEX_CONV = 'TEX_CONV'
    P_OUTPUT_FOLDER = 'OUTPUT_FOLDER'
    P_FILENAME = 'FILENAME'

    # 設定保存キー
    SETTING_KEY_ASSEMBLE = 'DDSExporter/TexAssemblePath'
    SETTING_KEY_CONV = 'DDSExporter/TexConvPath'

    # リスト定義 (降順)
    SIZE_OPTIONS = ['65536', '32768', '16384', '8192', '4096', '2048', '1024']
    
    FORMAT_NAMES = [
        'BC7 (高品質・推奨) - 地図に最適', 
        'BC1 / DXT1 (高圧縮) - アルファなし', 
        'BC3 / DXT5 (中圧縮) - 透過対応',
        'R8G8B8A8 (非圧縮) - 最高画質'
    ]
    
    FORMAT_CMDS = ['BC7_UNORM', 'BC1_UNORM', 'BC3_UNORM', 'R8G8B8A8_UNORM']

    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return ExportDDSCustomMips()

    def name(self):
        return 'export_dds_custom_mips_v16'

    def displayName(self):
        return self.tr('DDS画像作成')

    def group(self):
        return self.tr('User Scripts')

    def shortHelpString(self):
        return self.tr(
            "QGISのレンダリング機能を使って、ミップマップ付きのDDSを作成します。\n"
            "【重要】 <b>texassemble.exe</b> と <b>texconv.exe</b> が必要です。"
            "ない場合は<a href=\"https://github.com/microsoft/DirectXTex/releases\">GitHub</a>からダウンロードしてください。"
        )

    def initAlgorithm(self, config=None):
        settings = QgsSettings()
        default_assemble = settings.value(self.SETTING_KEY_ASSEMBLE, '', type=str)
        default_conv = settings.value(self.SETTING_KEY_CONV, '', type=str)

        # 1. 領域指定
        self.addParameter(
            QgsProcessingParameterExtent(
                self.P_EXTENT,
                self.tr('描画領域'),
                defaultValue=None
            )
        )

        # 2. サイズ
        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_SIZE_ENUM,
                self.tr('DDS画像サイズ (px)'),
                options=self.SIZE_OPTIONS,
                defaultValue=3 # 8192
            )
        )

        # カスタムサイズ
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_USE_CUSTOM,
                self.tr('カスタムサイズ (チェック時のみ数値を適用)'),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_WIDTH, self.tr('カスタム幅 (px)'),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=1920, optional=True, minValue=1
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_HEIGHT, self.tr('カスタム高さ (px)'),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=1080, optional=True, minValue=1
            )
        )

        # 形式
        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_FORMAT, self.tr('圧縮形式'),
                options=self.FORMAT_NAMES, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_MAX_LEVELS, self.tr('ミップマップ数'),
                type=QgsProcessingParameterNumber.Integer, defaultValue=8, minValue=1, maxValue=14
            )
        )

        # ファイル名
        self.addParameter(
            QgsProcessingParameterString(
                self.P_FILENAME,
                self.tr('保存ファイル名 (拡張子 .dds は自動付与)'),
                defaultValue='my_map'
            )
        )

        # 出力先
        self.addParameter(QgsProcessingParameterFolderDestination(self.P_OUTPUT_FOLDER, self.tr('出力先フォルダ')))

        # ツールパス
        self.addParameter(QgsProcessingParameterFile(
            self.P_TEX_ASSEMBLE, 
            self.tr('texassemble.exe のパス'), 
            fileFilter='Executables (*.exe)',
            defaultValue=default_assemble,
        ))
        
        self.addParameter(QgsProcessingParameterFile(
            self.P_TEX_CONV, 
            self.tr('texconv.exe のパス'), 
            fileFilter='Executables (*.exe)',
            defaultValue=default_conv,
        ))

    def processAlgorithm(self, parameters, context, feedback):
        # --- ツールパス処理 ---
        tex_assemble = self.parameterAsFile(parameters, self.P_TEX_ASSEMBLE, context)
        tex_conv = self.parameterAsFile(parameters, self.P_TEX_CONV, context)

        if not tex_assemble or not tex_conv:
             raise ValueError("【エラー】ツールパスが空欄です。")

        tex_assemble = os.path.normpath(tex_assemble.strip().strip('"').strip("'"))
        tex_conv = os.path.normpath(tex_conv.strip().strip('"').strip("'"))

        if not os.path.exists(tex_assemble): raise ValueError(f"ツールが見つかりません: {tex_assemble}")
        if not os.path.exists(tex_conv): raise ValueError(f"ツールが見つかりません: {tex_conv}")

        settings = QgsSettings()
        settings.setValue(self.SETTING_KEY_ASSEMBLE, tex_assemble)
        settings.setValue(self.SETTING_KEY_CONV, tex_conv)

        # --- パラメータ取得 ---
        extent = self.parameterAsExtent(parameters, self.P_EXTENT, context)
        if extent.isNull(): raise ValueError("【エラー】領域が指定されていません。")

        use_custom = self.parameterAsBool(parameters, self.P_USE_CUSTOM, context)
        if use_custom:
            start_w = self.parameterAsInt(parameters, self.P_WIDTH, context)
            start_h = self.parameterAsInt(parameters, self.P_HEIGHT, context)
        else:
            enum_idx = self.parameterAsInt(parameters, self.P_SIZE_ENUM, context)
            size = int(self.SIZE_OPTIONS[enum_idx])
            start_w = size
            start_h = size
            
        format_idx = self.parameterAsInt(parameters, self.P_FORMAT, context)
        format_cmd = self.FORMAT_CMDS[format_idx]
        max_levels = self.parameterAsInt(parameters, self.P_MAX_LEVELS, context)
        
        output_folder = self.parameterAsString(parameters, self.P_OUTPUT_FOLDER, context)
        user_filename = self.parameterAsString(parameters, self.P_FILENAME, context)
        
        if not user_filename: user_filename = "output_map"
        if user_filename.lower().endswith('.dds'): user_filename = user_filename[:-4]
        
        final_dds_path = os.path.normpath(os.path.join(output_folder, f"{user_filename}.dds"))
        
        feedback.pushInfo(f"Target Size: {start_w} x {start_h}")
        feedback.pushInfo(f"Final Output: {final_dds_path}")

        # --- 処理開始 ---
        with tempfile.TemporaryDirectory() as temp_dir:
            feedback.pushInfo(f"Using Temp Dir: {temp_dir}")
            
            project = context.project()
            
            try:
                layers = iface.mapCanvas().layers()
                # feedback.pushInfo("GUIキャンバスの表示設定(チェック状態)を反映します。")
            except:
                layers = project.mapThemeCollection().masterLayerOrder()
                # feedback.pushInfo("GUIコンテキスト外のため、全レイヤを出力します。")

            bg_color = project.backgroundColor()

            generated_pngs = []
            total_steps = max_levels + 2

            # --- Step 1: レンダリング ---
            feedback.setProgressText("Rendering...")
            
            for level in range(max_levels):
                if feedback.isCanceled(): return {}

                curr_w = int(start_w / (2 ** level))
                curr_h = int(start_h / (2 ** level))

                if curr_w < 4 or curr_h < 4: break

                settings = QgsMapSettings()
                settings.setLayers(layers)
                settings.setDestinationCrs(project.crs())
                settings.setExtent(extent)
                settings.setOutputSize(QSize(curr_w, curr_h))
                settings.setBackgroundColor(bg_color)

                job = QgsMapRendererParallelJob(settings)
                job.start()
                job.waitForFinished()

                img = job.renderedImage()
                out_path = os.path.join(temp_dir, f"mip{level}.png")
                img.save(out_path, "PNG")
                generated_pngs.append(out_path)
                
                feedback.setProgress((level / total_steps) * 100)

            # --- Step 2: 結合 ---
            feedback.pushInfo("Combining...")
            temp_dds_uncompressed = os.path.join(temp_dir, "temp_uncompressed.dds")
            
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            # コマンド: from-mips
            cmd_assemble = [tex_assemble, "from-mips", "-y", "-o", temp_dds_uncompressed] + generated_pngs
            
            proc_assemble = subprocess.run(
                cmd_assemble, 
                capture_output=True, 
                text=True, 
                encoding='cp932',
                startupinfo=startupinfo
            )
            
            if proc_assemble.returncode != 0:
                raise RuntimeError(f"texassemble エラー (Code {proc_assemble.returncode}):\n{proc_assemble.stderr}\n{proc_assemble.stdout}")

            # --- Step 3: 変換と移動 ---
            feedback.pushInfo("Compressing to Final Destination...")
            
            cmd_convert = [tex_conv, "-f", format_cmd, "-y", "-o", temp_dir, temp_dds_uncompressed]
            
            proc_convert = subprocess.run(
                cmd_convert,
                capture_output=True,
                text=True,
                encoding='cp932',
                startupinfo=startupinfo
            )
            
            if proc_convert.returncode != 0:
                 raise RuntimeError(f"texconv エラー (Code {proc_convert.returncode}):\n{proc_convert.stderr}")

            # 生成されたファイル特定
            expected_output = os.path.join(temp_dir, "temp_uncompressed.dds")
            if not os.path.exists(expected_output):
                expected_output_upper = os.path.join(temp_dir, "temp_uncompressed.DDS")
                if os.path.exists(expected_output_upper):
                    expected_output = expected_output_upper
                else:
                    files = os.listdir(temp_dir)
                    raise RuntimeError(f"圧縮後のファイルが見つかりません。一時フォルダ内容: {files}")
            
            # 移動先フォルダ作成と移動
            dest_dir = os.path.dirname(final_dds_path)
            if not os.path.exists(dest_dir):
                os.makedirs(dest_dir, exist_ok=True)
            
            shutil.move(expected_output, final_dds_path)

        return {self.P_OUTPUT_FOLDER: final_dds_path}
