class NotificationQueue {
    constructor(maxSize = 5) {
        this.maxSize = maxSize;
        this.queue = [];
        this.container = document.getElementById('notification-queue');
    }

    add(message, type) {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        
        // 添加到队列
        this.queue.push(notification);
        
        // 保持队列不超过最大长度
        if (this.queue.length > this.maxSize) {
            const removed = this.queue.shift();
            if (removed.parentNode === this.container) {
                this.container.removeChild(removed);
            }
        }
        
        // 添加到DOM
        this.container.appendChild(notification);
        
        // 5秒后自动移除
        setTimeout(() => {
            if (notification.parentNode === this.container) {
                this.container.removeChild(notification);
                this.queue = this.queue.filter(item => item !== notification);
            }
        }, 5000);
    }
}

class PrinterDetectApp {
    constructor() {
        this.ip = window.location.hostname; 
        this.frame_detect_pos_count = 0;
        this.frame_updated = false;
        this.frame_scores = 0;
        this.total_detect_find_count = 0;
        this.total_conf_count = 0;
        this.total_md_count = 0;
        this.no_pei = 0;
        this.printer_connect_status = false;
        this.printing_status = false;
        this.errorStartTime = null;
        this.isRequesting = false;  // 新增：标记是否正在请求
        // 新增：记录上一次 frame_detect_pos_count 变化的时间
        this.lastFrameChangeTime = Date.now() - 5000;
        // 新增：定时器 ID
        this.checkFrameTimer = null;
        this.notificationQueue = new NotificationQueue();
        this.initVersionIcon();
        this.init();
    }

    async getVersion() {
        try {
            const response = await fetch(`http://${this.ip}:9010/version`);
            if (response.ok) {
                const data = await response.json();
                this.notificationQueue.add(`软件版本: ${data.sw_version}`, "version");
            } else {
                this.notificationQueue.add(`获取版本信息失败，状态码: ${response.status}`, "error");
            }
        } catch (error) {
            this.notificationQueue.add(`获取版本信息出错: ${error}`, "error");
        }
    }

    initVersionIcon() {
        const versionIcon = document.getElementById('version-icon');
        if (versionIcon) {
            versionIcon.addEventListener('click', () => this.getVersion());
        }
    }

    async detectionResUpdate() {
        if (this.isRequesting) return;  // 如果正在请求，则跳过
        this.isRequesting = true;  // 标记为正在请求

        try {
            const response = await fetch(`http://${this.ip}:9010/detection_res`);
            if (response.ok) {
                const data = await response.json();
                this.printer_connect_status = true;
                this.printing_status = data.printing_status.toLowerCase() === 'true';
                this.print_progress = parseFloat(data.print_progress);

                const oldFrameCount = this.frame_detect_pos_count;
                this.frame_detect_pos_count = parseInt(data.frame_detect_pos_count);

                if (oldFrameCount !== this.frame_detect_pos_count) {
                    this.lastFrameChangeTime = Date.now();
                    this.frame_updated = true;
                    this.frame_scores = parseFloat(data.frame_scores);
                    this.total_detect_find_count = parseInt(data.total_detect_find_count);
                    this.total_conf_count = parseFloat(data.total_conf_count);
                    this.total_md_count = parseInt(data.total_md_count);
                    this.no_pei = parseInt(data.no_pei);
                    this.foreign_count = parseInt(data.foreign_count);
                    
                    // 触发弹窗通知
                    if (this.no_pei > 0) {
                        this.notificationQueue.add("No PEI detected", "no-pei");
                    }
                    if (this.foreign_count > 0) {
                        this.notificationQueue.add("Foreign object detected", "foreign");
                    }
                    
                    // 只有当检测帧数变化时才更新历史图片
                    await this.updateHistoryImages();
                } else {
                    // 检查是否已经 3 秒未变化
                    if (Date.now() - this.lastFrameChangeTime >= 3000) {
                        await this.updateImage();
                        this.lastFrameChangeTime = Date.now();
                    }
                }

                this.updateUI();
            } else {
                this.printer_connect_status = false;
                console.error(`请求失败，状态码: ${response.status}`);
            }
        } catch (error) {
            console.error(`请求出错: ${error}`);
        } finally {
            this.isRequesting = false;  // 请求完成，重置标记
        }
    }

    updateUI() {
        const statusElement = document.getElementById('printing-status');
        const progressElement = document.getElementById('progress');
        const errorElement = document.getElementById('error-message');

        if (this.printing_status) {
            statusElement.textContent = `打印状态: 打印中 ${this.print_progress.toFixed(2)}%`;
            progressElement.style.width = `${this.print_progress}%`;
        } else {
            statusElement.textContent = "打印状态: 未打印";
            progressElement.style.width = "0%";
        }

        if (this.frame_updated) {
            this.frame_updated = false;
            this.updateImage();
        }
    }

    async updateImage() {
        try {
            const response = await fetch(`http://${this.ip}:9010/capture`);
            if (response.ok) {
                const data = await response.json(); // 假设返回的是 JSON 数据
                const imgUrl = `data:image/jpeg;base64,${data.image}`; // 从 base64 数据创建图片 URL
                const imgElement = document.getElementById('capture-image');
                const captionElement = document.getElementById('image-caption');

                const score = data.detection_score !== undefined ? parseFloat(data.detection_score) : 'N/A';

                imgElement.src = imgUrl;
                // 更新图片说明，添加检测得分
                captionElement.textContent = `score:${score}\n检测帧数:${this.frame_detect_pos_count}\n异常次数:${this.total_detect_find_count}\n
                总分数:${this.total_conf_count}\nMD:${this.total_md_count}\nNO_PEI:${this.no_pei}\n异物:${this.foreign_count}`;

                // 清除错误信息
                document.getElementById('error-message').style.display = 'none';
                this.errorStartTime = null;
            } else {
                throw new Error(`请求失败，状态码: ${response.status}`);
            }
        } catch (error) {
            if (!this.errorStartTime) {
                this.errorStartTime = Date.now();
            }
            const elapsedTime = (Date.now() - this.errorStartTime) / 1000;
            const errorElement = document.getElementById('error-message');
            errorElement.textContent = `发生错误，已持续 ${elapsedTime.toFixed(2)} 秒: ${error}`;
            errorElement.style.display = 'block';
        }
    }

    async updateHistoryImages() {
        try {
            // 获取炒面历史帧
            const noodleResponse = await fetch(`http://${this.ip}:9010/history_frames?type=noodle`);
            // 获取has_pei历史帧
            const noPeiResponse = await fetch(`http://${this.ip}:9010/history_frames?type=has_pei`);
            // 获取foreign历史帧
            const foreignResponse = await fetch(`http://${this.ip}:9010/history_frames?type=foreign`);

            if (noodleResponse.ok && noPeiResponse.ok && foreignResponse.ok) {
                const noodleData = await noodleResponse.json();
                const hasPeiData = await noPeiResponse.json();
                const foreignData = await foreignResponse.json();
                
                // 更新炒面历史帧容器
                const noodleContainer = document.getElementById('noodle-history-container');
                noodleContainer.innerHTML = '';
                noodleData.forEach((frameData) => {
                    const imgUrl = `data:image/jpeg;base64,${frameData.image}`;
                    const img = document.createElement('img');
                    img.src = imgUrl;
                    img.classList.add('history-image');
                    noodleContainer.appendChild(img);
                });
                
                // 更新has_pei历史帧容器
                const hasPeiContainer = document.getElementById('has-pei-history-container');
                hasPeiContainer.innerHTML = '';
                hasPeiData.forEach((frameData) => {
                    const imgUrl = `data:image/jpeg;base64,${frameData.image}`;
                    const img = document.createElement('img');
                    img.src = imgUrl;
                    img.classList.add('history-image');
                    hasPeiContainer.appendChild(img);
                });

                // 更新异物历史帧容器
                const foreignContainer = document.getElementById('foreign-history-container');
                foreignContainer.innerHTML = '';
                foreignData.forEach((frameData) => {
                    const imgUrl = `data:image/jpeg;base64,${frameData.image}`;
                    const img = document.createElement('img');
                    img.src = imgUrl;
                    img.classList.add('history-image');
                    foreignContainer.appendChild(img);
                });
            } else {
                console.error(`获取历史图片失败`);
            }
        } catch (error) {
            console.error(`获取历史图片出错: ${error}`);
        }
    }

    init() {
        setInterval(() => this.detectionResUpdate(), 1000);  // 将间隔时间改为 2000ms
    }
}

new PrinterDetectApp();