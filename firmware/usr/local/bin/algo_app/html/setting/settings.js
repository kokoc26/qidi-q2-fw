class ConfigHandler {
    constructor() {
        this.ip = window.location.hostname; 
        this.url = `http://${this.ip}:9010/config`;
    }

    async readConfig() {
        try {
            const response = await fetch(this.url);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const config = await response.json();
            return config;
        } catch (error) {
            console.error(`请求配置文件时出错: ${error}`);
            return {};
        }
    }

    async writeConfig(config) {
        try {
            const response = await fetch(this.url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(config, null, 2)
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            console.log("配置已更新并写回服务器。");
            return true;
        } catch (error) {
            console.error(`写入配置文件时出错: ${error}`);
            return false;
        }
    }
}

document.addEventListener('DOMContentLoaded', async function() {
    const configHandler = new ConfigHandler();
    
    // 初始化所有滑块和复选框
    const settings = {
        isDetectFlag: document.getElementById('is-detect-flag'),
        inferenceSettings: document.getElementById('inference-settings'),
        detectActionAlarmOpen: document.getElementById('detect-action-alarm-open'),
        alarmSettings: document.getElementById('alarm-settings'),
        isSaveRawVideo: document.getElementById('is-save-raw-video'),
        isSaveLayerBestView: document.getElementById('is-save-layer-best-view'),
        saveRawVideoInterval: document.getElementById('save-raw-video-interval'),
        isSaveDetectVideo: document.getElementById('is-save-detect-video'),
        saveDetectVideoInterval: document.getElementById('save-detect-video-interval'),
        saveDetectPicToVideo: document.getElementById('save-detect-pic-to-video'),
        // 新增：获取 PEI 检测和异物检测勾选框
        isPeiCheck: document.getElementById('is-pei-check'),
        isForeignCheck: document.getElementById('is-foreign-check')
    };

    // 从服务器读取配置
    let config = await configHandler.readConfig();
    if (Object.keys(config).length === 0) {
        alert('无法从服务器读取配置，请检查服务器连接');
        return;
    }
    console.log('从服务器读取的配置:', config);

    // 初始化UI状态
    function initializeUI() {
        // 推理设置
        settings.isDetectFlag.checked = config.detect_setting.is_detect_flag.toLowerCase() === 'true';
        settings.inferenceSettings.style.display = settings.isDetectFlag.checked ? 'block' : 'none';
        
        // 是否开启最小遮挡帧用于检测
        document.getElementById('min-occluded-frame').checked = config.video_input.min_occluded_frame.toLowerCase() === 'true';

        // 是否开启模型移动检测
        document.getElementById('is-md-check').checked = config.detect_setting.is_md_check.toLowerCase() === 'true';

        // 更新start-layer值及其显示
        const startLayer = parseFloat(config.detect_setting.start_layer || 1);
        document.getElementById('start-layer').value = startLayer;
        document.getElementById('start-layer-value').textContent = startLayer;

        // 更新conf-thres值及其显示
        const confThres = parseFloat(config.detect_setting.detect_config.conf_thres || 0.5);
        document.getElementById('conf-thres').value = confThres;
        document.getElementById('conf-thres-value').textContent = confThres;
        
        // 更新iou-thres值及其显示
        const iouThres = parseFloat(config.detect_setting.detect_config.iou_thres || 0.5);
        document.getElementById('iou-thres').value = iouThres;
        document.getElementById('iou-thres-value').textContent = iouThres;
        
        // 更新intra-num值及其显示
        const intraNum = parseInt(config.detect_setting.detect_config.intra_num || 4);
        document.getElementById('intra-num').value = intraNum;
        document.getElementById('intra-num-value').textContent = intraNum;
    
        // 告警设置
        settings.detectActionAlarmOpen.checked = config.detect_setting.detect_action.alarm.is_open_alarm.toLowerCase() === 'true';
        settings.alarmSettings.style.display = settings.detectActionAlarmOpen.checked ? 'block' : 'none';
        
        // 更新detect-action-alarm-count值及其显示
        const alarmCount = parseInt(config.detect_setting.detect_action.alarm.count || 10);
        document.getElementById('detect-action-alarm-count').value = alarmCount;
        document.getElementById('detect-action-alarm-count-value').textContent = alarmCount;
        
        // 更新detect-action-alarm-conf值及其显示
        const alarmConf = parseInt(config.detect_setting.detect_action.alarm.conf || 50);
        document.getElementById('detect-action-alarm-conf').value = alarmConf;
        document.getElementById('detect-action-alarm-conf-value').textContent = alarmConf;
    
        // 检测动作设置
        // 更新detect-action-find-count-count值及其显示
        const findCountCount = parseInt(config.detect_setting.detect_action.find_count.count || 20);
        document.getElementById('detect-action-find-count-count').value = findCountCount;
        document.getElementById('detect-action-find-count-count-value').textContent = findCountCount;
        
        document.getElementById('detect-action-find-count-pause').checked = config.detect_setting.detect_action.find_count.is_pause_print.toLowerCase() === 'true';
        document.getElementById('detect-action-find-count-cancel').checked = config.detect_setting.detect_action.find_count.is_cancel_print.toLowerCase() === 'true';
        
        // 更新detect-action-conf-count-conf值及其显示
        const confCountConf = parseInt(config.detect_setting.detect_action.conf_count.conf || 100);
        document.getElementById('detect-action-conf-count-conf').value = confCountConf;
        document.getElementById('detect-action-conf-count-conf-value').textContent = confCountConf;
        
        document.getElementById('detect-action-conf-count-pause').checked = config.detect_setting.detect_action.conf_count.is_pause_print.toLowerCase() === 'true';
        document.getElementById('detect-action-conf-count-cancel').checked = config.detect_setting.detect_action.conf_count.is_cancel_print.toLowerCase() === 'true';

        // 更新detect-action-md-count-count值及其显示
        const mdCountCount = parseInt(config.detect_setting.detect_action.md_count.count || 20);
        document.getElementById('detect-action-md-count-count').value = mdCountCount;
        document.getElementById('detect-action-md-count-count-value').textContent = mdCountCount;
        
        document.getElementById('detect-action-md-count-pause').checked = config.detect_setting.detect_action.md_count.is_pause_print.toLowerCase() === 'true';
        document.getElementById('detect-action-md-count-cancel').checked = config.detect_setting.detect_action.md_count.is_cancel_print.toLowerCase() === 'true';
        // 原始视频配置初始化
        if (config.video_output && config.video_output.video_output_raw) {
            settings.isSaveRawVideo.checked = config.video_output.video_output_raw.is_save_raw_video?.toLowerCase() === 'true';
            const saveRawVideoInterval = parseInt(config.video_output.video_output_raw.save_raw_video_interval) || 1000;
            settings.saveRawVideoInterval.value = saveRawVideoInterval;
            settings.isSaveLayerBestView.checked = config.video_output.video_output_raw.is_save_layer_best_view?.toLowerCase() === 'true';
        }

        // 检测后视频配置初始化
        if (config.video_output && config.video_output.video_output_detect) {
            settings.isSaveDetectVideo.checked = config.video_output.video_output_detect.is_save_detect_video?.toLowerCase() === 'true';
            const saveDetectVideoInterval = parseInt(config.video_output.video_output_detect.save_detect_video_interval) || 1000;
            settings.saveDetectVideoInterval.value = saveDetectVideoInterval;
            settings.saveDetectPicToVideo.checked = config.video_output.video_output_detect.save_detect_pic_to_video?.toLowerCase() === 'true';
        }

        // 新增：正确初始化 PEI 检测和异物检测勾选框
        document.getElementById('is-pei-check').checked = config.detect_setting.is_pei_check.toLowerCase() === 'true';
        settings.isForeignCheck.checked = config.detect_setting.is_foreign_check.toLowerCase() === 'true';
    }

    // 显示/隐藏推理设置
    settings.isDetectFlag.addEventListener('change', function() {
        settings.inferenceSettings.style.display = this.checked ? 'block' : 'none';
    });

    // 显示/隐藏告警设置
    settings.detectActionAlarmOpen.addEventListener('change', function() {
        settings.alarmSettings.style.display = this.checked ? 'block' : 'none';
    });

    // 更新所有滑块值显示
    document.querySelectorAll('input[type="range"]').forEach(slider => {
        const valueDisplay = document.getElementById(slider.id + '-value');
        valueDisplay.textContent = slider.value;
        slider.addEventListener('input', () => {
            valueDisplay.textContent = slider.value;
        });
    });

    // 保存设置
    document.getElementById('save-settings').addEventListener('click', async function() {
        const updatedConfig = {
            detect_setting: {
                is_detect_flag: settings.isDetectFlag.checked.toString(),
                is_md_check: document.getElementById('is-md-check').checked.toString(), 
                start_layer: document.getElementById('start-layer').value.toString(),
                // model_name: document.getElementById('model-name').value,
                detect_config: {
                    conf_thres: document.getElementById('conf-thres').value.toString(),
                    iou_thres: document.getElementById('iou-thres').value.toString(),
                    intra_num: document.getElementById('intra-num').value.toString(),
                    inter_num: document.getElementById('intra-num').value.toString()
                },
                detect_action: {
                    alarm: {
                        is_open_alarm: settings.detectActionAlarmOpen.checked.toString(),
                        count: document.getElementById('detect-action-alarm-count').value.toString(),
                        conf: document.getElementById('detect-action-alarm-conf').value.toString()
                    },
                    find_count: {
                        count: document.getElementById('detect-action-find-count-count').value.toString(),
                        is_pause_print: document.getElementById('detect-action-find-count-pause').checked.toString(),
                        is_cancel_print: document.getElementById('detect-action-find-count-cancel').checked.toString()
                    },
                    conf_count: {
                        conf: document.getElementById('detect-action-conf-count-conf').value.toString(),
                        is_pause_print: document.getElementById('detect-action-conf-count-pause').checked.toString(),
                        is_cancel_print: document.getElementById('detect-action-conf-count-cancel').checked.toString()
                    },
                    md_count: {
                        count: document.getElementById('detect-action-md-count-count').value.toString(),
                        is_pause_print: document.getElementById('detect-action-md-count-pause').checked.toString(),
                        is_cancel_print: document.getElementById('detect-action-md-count-cancel').checked.toString()
                    },
                },
                // 新增：保存 PEI 检测和异物检测勾选框状态
                is_pei_check: settings.isPeiCheck.checked.toString(),
                is_foreign_check: settings.isForeignCheck.checked.toString()
            },
            video_input: {
                min_occluded_frame: document.getElementById('min-occluded-frame').checked.toString(), 
            },
            video_output: {
                video_output_raw: {
                    is_save_raw_video: settings.isSaveRawVideo.checked.toString(),
                    save_raw_video_interval: settings.saveRawVideoInterval.value.toString(),
                    is_save_layer_best_view: settings.isSaveLayerBestView.checked.toString()
                },
                video_output_detect: {
                    is_save_detect_video: settings.isSaveDetectVideo.checked.toString(),
                    save_detect_video_interval: settings.saveDetectVideoInterval.value.toString(),
                    save_detect_pic_to_video: settings.saveDetectPicToVideo.checked.toString()
                }
            }
        };

        const success = await configHandler.writeConfig(updatedConfig);
        if (success) {
            alert('设置已保存');
            config = updatedConfig; // 更新本地配置
        } else {
            alert('保存设置失败，请检查服务器连接');
        }
    });

    // 新增：视频列表相关元素
    const videoList = document.getElementById('video-list');
    const refreshButton = document.getElementById('refresh-video-list');

    // 新增：获取视频列表
    async function getVideoList() {
        try {
            const ip = window.location.hostname;
            const response = await fetch(`http://${ip}:9010/get-video-list`);
            const data = await response.json();
            if (response.ok) {
                renderVideoList(data);
            } else {
                alert('获取视频列表失败');
            }
        } catch (error) {
            console.error('获取视频列表出错:', error);
            alert('获取视频列表出错');
        }
    }

    // 新增：渲染视频列表
    function renderVideoList(videoFiles) {
        videoList.innerHTML = '';
        videoFiles.forEach(file => {
            const li = document.createElement('li');
            li.textContent = file;
            const downloadButton = document.createElement('button');
            downloadButton.textContent = '下载';
            downloadButton.addEventListener('click', () => downloadVideo(file));
            li.appendChild(downloadButton);
            const deleteButton = document.createElement('button');
            deleteButton.textContent = '删除';
            deleteButton.addEventListener('click', () => deleteVideo(file));
            li.appendChild(deleteButton);
            videoList.appendChild(li);
        });
    }

    // 新增：下载视频
    async function downloadVideo(fileName) {
        const ip = window.location.hostname;
        const url = `http://${ip}:9010/download-video/${encodeURIComponent(fileName)}`;
        const response = await fetch(url);
        if (response.ok) {
            const blob = await response.blob();
            const urlObject = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = urlObject;
            a.download = fileName;
            a.click();
            window.URL.revokeObjectURL(urlObject);
        } else {
            alert('下载视频失败');
        }
    }

    // 新增：删除视频
    async function deleteVideo(fileName) {
        if (confirm(`确定要删除 ${fileName} 吗？`)) {
            const ip = window.location.hostname;
            const response = await fetch(`http://${ip}:9010/delete-video/${encodeURIComponent(fileName)}`, {
                method: 'DELETE'
            });
            if (response.ok) {
                alert('视频删除成功');
                await getVideoList();
            } else {
                alert('删除视频失败');
            }
        }
    }

    // 新增：刷新视频列表按钮事件监听
    refreshButton.addEventListener('click', getVideoList);

    // 新增：初始化时获取视频列表
    getVideoList();
    
    // 初始化UI
    initializeUI();
});
