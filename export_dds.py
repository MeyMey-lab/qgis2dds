from qgis.PyQt.QtCore import QCoreApplication, QSize
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterExtent,
    QgsProcessingParameterBoolean,
    QgsProject,
    QgsMapSettings,
    QgsMapRendererSequentialJob,
    QgsRectangle,
    QgsSettings
)
import os
import math
import subprocess
import tempfile
import shutil
import time

# GUI構築時のみ使用するため安全にインポート
from qgis.utils import iface

class ExportDDSCustomMips_v31_SequentialFix(QgsProcessingAlgorithm):

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

    # レイヤ制御用
    P_HIDE_L1 = 'HIDE_L1'
    P_HIDE_L2 = 'HIDE_L2'
    P_HIDE_L3 = 'HIDE_L3'
    P_HIDE_L4 = 'HIDE_L4'
    P_HIDE_L5 = 'HIDE_L5'
    P_HIDE_L6 = 'HIDE_L6'
    
    # IDリスト受け渡し用の隠しパラメータ
    P_VISIBLE_IDS = 'VISIBLE_IDS_HIDDEN'

    # 設定保存キー
    SETTING_KEY_ASSEMBLE = 'DDSExporter/TexAssemblePath'
    SETTING_KEY_CONV = 'DDSExporter/TexConvPath'

    # リスト定義
    SIZE_OPTIONS = ['32768', '16384', '8192', '4096', '2048', '1024', '512', '256']
    MIP_OPTIONS = ['自動 (最大まで生成)', 'なし (ベース画像のみ)'] + [str(i) for i in range(1, 13)]
    
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
        return ExportDDSCustomMips_v31_SequentialFix()

    def name(self):
        return 'export_dds_custom_mips_v31'

    def displayName(self):
        return self.tr('DDS画像作成 v31 (安定版)')

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

        self.addParameter(QgsProcessingParameterExtent(self.P_EXTENT, self.tr('描画領域'), defaultValue=None))
        self.addParameter(QgsProcessingParameterEnum(self.P_SIZE_ENUM, self.tr('DDS画像サイズ (px)'), options=self.SIZE_OPTIONS, defaultValue=3))
        
        self.addParameter(QgsProcessingParameterBoolean(self.P_USE_CUSTOM, self.tr('カスタムサイズ (チェック時のみ数値を適用)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(self.P_WIDTH, self.tr('カスタム幅 (px)'), type=QgsProcessingParameterNumber.Integer, defaultValue=1920, optional=True, minValue=1))
        self.addParameter(QgsProcessingParameterNumber(self.P_HEIGHT, self.tr('カスタム高さ (px)'), type=QgsProcessingParameterNumber.Integer, defaultValue=1080, optional=True, minValue=1))

        self.addParameter(QgsProcessingParameterEnum(self.P_FORMAT, self.tr('圧縮形式'), options=self.FORMAT_NAMES, defaultValue=0))
        
        self.addParameter(QgsProcessingParameterEnum(self.P_MAX_LEVELS, self.tr('ミップマップ Level数'), options=self.MIP_OPTIONS, defaultValue=0))
        
        self.addParameter(QgsProcessingParameterString(self.P_FILENAME, self.tr('保存ファイル名 (拡張子 .dds は自動付与)'), defaultValue='my_map'))
        self.addParameter(QgsProcessingParameterFolderDestination(self.P_OUTPUT_FOLDER, self.tr('出力先フォルダ')))

        # =====================================================================
        #  高度なパラメータ (折りたたみ)
        # =====================================================================
        
        # 動的リスト生成ロジック (Main Threadで実行)
        layer_names = []
        layer_ids_str = ""
        
        try:
            # GUIキャンバスからチェック済みレイヤを取得
            layers = iface.mapCanvas().layers()
            id_list = []
            for l in layers:
                layer_names.append(l.name())
                id_list.append(l.id())
            layer_ids_str = ",".join(id_list)
        except:
            # GUIがない場合のフォールバック
            layers = QgsProject.instance().mapLayers().values()
            id_list = []
            for l in layers:
                layer_names.append(l.name())
                id_list.append(l.id())
            layer_ids_str = ",".join(id_list)

        # 隠しパラメータ: レイヤIDリスト
        param_ids = QgsProcessingParameterString(self.P_VISIBLE_IDS, "Hidden IDs", defaultValue=layer_ids_str)
        param_ids.setFlags(param_ids.flags() | QgsProcessingParameterDefinition.FlagHidden)
        self.addParameter(param_ids)

        # レイヤ選択プルダウン (Enumを使用)
        param_l1 = QgsProcessingParameterEnum(self.P_HIDE_L1, self.tr('Level 1 (1/2サイズ) 以降で隠すレイヤ'), options=layer_names, allowMultiple=True, optional=True)
        param_l1.setFlags(param_l1.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_l1)

        param_l2 = QgsProcessingParameterEnum(self.P_HIDE_L2, self.tr('Level 2 (1/4サイズ) 以降で隠すレイヤ'), options=layer_names, allowMultiple=True, optional=True)
        param_l2.setFlags(param_l2.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_l2)

        param_l3 = QgsProcessingParameterEnum(self.P_HIDE_L3, self.tr('Level 3 (1/8サイズ) 以降で隠すレイヤ'), options=layer_names, allowMultiple=True, optional=True)
        param_l3.setFlags(param_l3.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_l3)

        param_l4 = QgsProcessingParameterEnum(self.P_HIDE_L4, self.tr('Level 4 (1/16サイズ) 以降で隠すレイヤ'), options=layer_names, allowMultiple=True, optional=True)
        param_l4.setFlags(param_l4.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_l4)

        param_l5 = QgsProcessingParameterEnum(self.P_HIDE_L5, self.tr('Level 5 (1/32サイズ) 以降で隠すレイヤ'), options=layer_names, allowMultiple=True, optional=True)
        param_l5.setFlags(param_l5.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_l5)

        param_l6 = QgsProcessingParameterEnum(self.P_HIDE_L6, self.tr('Level 6 (1/64サイズ) 以降で隠すレイヤ'), options=layer_names, allowMultiple=True, optional=True)
        param_l6.setFlags(param_l6.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_l6)

        # ツールパス
        param_assemble = QgsProcessingParameterFile(self.P_TEX_ASSEMBLE, self.tr('texassemble.exe のパス'), fileFilter='Executables (*.exe)', defaultValue=default_assemble)
        param_assemble.setFlags(param_assemble.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_assemble)

        param_conv = QgsProcessingParameterFile(self.P_TEX_CONV, self.tr('texconv.exe のパス'), fileFilter='Executables (*.exe)', defaultValue=default_conv)
        param_conv.setFlags(param_conv.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(param_conv)

    def processAlgorithm(self, parameters, context, feedback):
        # --- ツールパス処理 ---
        tex_assemble = self.parameterAsFile(parameters, self.P_TEX_ASSEMBLE, context)
        tex_conv = self.parameterAsFile(parameters, self.P_TEX_CONV, context)

        if not tex_assemble or not tex_conv: return self.report_error("ツールパスが空欄です。", feedback)

        tex_assemble = os.path.normpath(tex_assemble.strip().strip('"').strip("'"))
        tex_conv = os.path.normpath(tex_conv.strip().strip('"').strip("'"))

        if not os.path.exists(tex_assemble): return self.report_error(f"ツールが見つかりません: {tex_assemble}", feedback)
        if not os.path.exists(tex_conv): return self.report_error(f"ツールが見つかりません: {tex_conv}", feedback)

        settings = QgsSettings()
        settings.setValue(self.SETTING_KEY_ASSEMBLE, tex_assemble)
        settings.setValue(self.SETTING_KEY_CONV, tex_conv)

        # --- パラメータ取得 ---
        extent = self.parameterAsExtent(parameters, self.P_EXTENT, context)
        if extent.isNull(): return self.report_error("領域が指定されていません。", feedback)

        use_custom = self.parameterAsBool(parameters, self.P_USE_CUSTOM, context)
        if use_custom:
            start_w = self.parameterAsInt(parameters, self.P_WIDTH, context)
            start_h = self.parameterAsInt(parameters, self.P_HEIGHT, context)
        else:
            enum_idx = self.parameterAsInt(parameters, self.P_SIZE_ENUM, context)
            size = int(self.SIZE_OPTIONS[enum_idx])
            start_w = size
            start_h = size
            
        mip_index = self.parameterAsInt(parameters, self.P_MAX_LEVELS, context)
        
        if mip_index == 0:
            max_size = max(start_w, start_h)
            max_levels = int(math.log2(max_size)) + 1
            feedback.pushInfo(f"Mipmap: Auto (Max {max_levels} levels)")
        else:
            max_levels = mip_index
            if max_levels == 1:
                feedback.pushInfo("Mipmap: None (Base image only)")
            else:
                feedback.pushInfo(f"Mipmap: Fixed {max_levels - 1} levels (Total {max_levels} images)")
            
        format_idx = self.parameterAsInt(parameters, self.P_FORMAT, context)
        format_cmd = self.FORMAT_CMDS[format_idx]
        
        output_folder = self.parameterAsString(parameters, self.P_OUTPUT_FOLDER, context)
        user_filename = self.parameterAsString(parameters, self.P_FILENAME, context)
        
        if not user_filename: user_filename = "output_map"
        if user_filename.lower().endswith('.dds'): user_filename = user_filename[:-4]
        
        final_dds_path = os.path.normpath(os.path.join(output_folder, f"{user_filename}.dds"))
        
        # --- IDリスト復元 ---
        ids_str = self.parameterAsString(parameters, self.P_VISIBLE_IDS, context)
        reference_layer_ids = ids_str.split(',') if ids_str else []

        l1_indices = self.parameterAsEnums(parameters, self.P_HIDE_L1, context)
        l2_indices = self.parameterAsEnums(parameters, self.P_HIDE_L2, context)
        l3_indices = self.parameterAsEnums(parameters, self.P_HIDE_L3, context)
        l4_indices = self.parameterAsEnums(parameters, self.P_HIDE_L4, context)
        l5_indices = self.parameterAsEnums(parameters, self.P_HIDE_L5, context)
        l6_indices = self.parameterAsEnums(parameters, self.P_HIDE_L6, context)

        hide_rules_ids = {
            1: set(reference_layer_ids[i] for i in l1_indices if i < len(reference_layer_ids)),
            2: set(reference_layer_ids[i] for i in l2_indices if i < len(reference_layer_ids)),
            3: set(reference_layer_ids[i] for i in l3_indices if i < len(reference_layer_ids)),
            4: set(reference_layer_ids[i] for i in l4_indices if i < len(reference_layer_ids)),
            5: set(reference_layer_ids[i] for i in l5_indices if i < len(reference_layer_ids)),
            6: set(reference_layer_ids[i] for i in l6_indices if i < len(reference_layer_ids)),
        }

        feedback.pushInfo(f"Target Size: {start_w} x {start_h}")
        feedback.pushInfo(f"Final Output: {final_dds_path}")

        # --- 処理開始 ---
        with tempfile.TemporaryDirectory() as temp_dir:
            feedback.pushInfo(f"Using Temp Dir: {temp_dir}")
            
            project = context.project()
            bg_color = project.backgroundColor()

            generated_pngs = []
            total_steps = max_levels + 2

            # --- Step 1: レンダリング ---
            feedback.setProgressText("Rendering...")
            
            for level in range(max_levels):
                if feedback.isCanceled(): return {}

                curr_w = int(start_w / (2 ** level))
                curr_h = int(start_h / (2 ** level))

                # texconvは1x1まで許容
                if curr_w < 1 or curr_h < 1: break

                # レイヤフィルタリング
                hidden_ids_current = set()
                for rule_level, ids in hide_rules_ids.items():
                    if level >= rule_level: 
                        hidden_ids_current.update(ids)
                
                active_layers = []
                for lid in reference_layer_ids:
                    if lid not in hidden_ids_current:
                        lyr = project.mapLayer(lid)
                        if lyr:
                            active_layers.append(lyr)

                settings = QgsMapSettings()
                settings.setLayers(active_layers)
                settings.setDestinationCrs(project.crs())
                settings.setExtent(extent)
                settings.setOutputSize(QSize(curr_w, curr_h))
                settings.setBackgroundColor(bg_color)

                # ★修正: 安全性の高い順次レンダリングを使用
                job = QgsMapRendererSequentialJob(settings)
                job.start()
                job.waitForFinished()

                img = job.renderedImage()
                out_path = os.path.join(temp_dir, f"mip{level}.png")
                img.save(out_path, "PNG")
                generated_pngs.append(out_path)
                
                feedback.setProgress((level / total_steps) * 100)

            # --- Step 2: 結合 (texassemble) ---
            feedback.pushInfo("Combining...")
            temp_dds_uncompressed = os.path.join(temp_dir, "temp_uncompressed.dds")
            
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            cmd_assemble = [tex_assemble, "from-mips", "-y", "-o", temp_dds_uncompressed] + generated_pngs
            
            proc_assemble = subprocess.run(
                cmd_assemble, 
                capture_output=True, 
                text=True, 
                encoding='cp932',
                startupinfo=startupinfo
            )
            
            if proc_assemble.returncode != 0:
                return self.report_error(f"texassemble エラー:\n{proc_assemble.stderr}\n{proc_assemble.stdout}", feedback)

            if not os.path.exists(temp_dds_uncompressed) or os.path.getsize(temp_dds_uncompressed) == 0:
                return self.report_error(f"texassemble: ファイル生成失敗。\nログ:\n{proc_assemble.stdout}", feedback)

            time.sleep(1.0) 

            # --- Step 3: 変換と移動 (texconv) ---
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
                 return self.report_error(f"texconv エラー:\n{proc_convert.stderr}\n{proc_convert.stdout}", feedback)

            expected_output = os.path.join(temp_dir, "temp_uncompressed.dds")
            if not os.path.exists(expected_output):
                expected_output_upper = os.path.join(temp_dir, "temp_uncompressed.DDS")
                if os.path.exists(expected_output_upper):
                    expected_output = expected_output_upper
                else:
                    return self.report_error(f"圧縮後のファイルが見つかりません。一時フォルダ内容: {os.listdir(temp_dir)}", feedback)
            
            dest_dir = os.path.dirname(final_dds_path)
            if not os.path.exists(dest_dir):
                os.makedirs(dest_dir, exist_ok=True)
            
            if os.path.exists(final_dds_path):
                try:
                    os.remove(final_dds_path)
                except:
                    pass 
            
            shutil.move(expected_output, final_dds_path)

        return {self.P_OUTPUT_FOLDER: final_dds_path}

    def report_error(self, msg, feedback):
        feedback.reportError(msg)
        return {}
