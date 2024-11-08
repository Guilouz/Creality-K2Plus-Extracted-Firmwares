import logging
import http.client
from email.mime.base import MIMEBase
from email.encoders import encode_base64
import os
import subprocess
import json
import re
import copy
import threading
from subprocess import check_output

def extract_AI_switch_value(json_file):
    # 读取 JSON 文件内容
    try:
        with open(json_file, 'r') as file:
            data = json.load(file)
    except Exception as e:
        logging.info(f"Error opening or reading the JSON file: {e}")
        return -1

    # 查找 ai_control 中的 switch 值
    try:
        switch_value = data['ai_control']['switch']
        # logging.info(f"switch: {switch_value}")
        return switch_value
    except KeyError as e:
        logging.info(f"Key error: {e} not found in the JSON data")
        return -2
    
def nozzle_cam_power_on():
    try:
        logging.info("nozzle_cam_power.sh on")
        result_capture = subprocess.run(['nozzle_cam_power.sh', 'on'], capture_output=True, text=True)
        # 打印 ai_capture 的输出
        logging.info(result_capture.stdout)
        logging.info(result_capture.stderr)

    except Exception as e:
        logging.info(f"Error: {e}")

def nozzle_cam_power_off():
    try:
        logging.info("nozzle_cam_power.sh off")
        result_capture = subprocess.run(['nozzle_cam_power.sh', 'off'], capture_output=True, text=True)
        # 打印 ai_capture 的输出
        logging.info(result_capture.stdout)
        logging.info(result_capture.stderr)

    except Exception as e:
        logging.info(f"Error: {e}")
def ai_capture():
    try:
        logging.info("ai_capture 1")
        # 运行 ai_capture 命令并捕获输出
        result_capture = subprocess.run(['ai_capture', '1'], capture_output=True, text=True)

        # 打印 ai_capture 的输出（可选）
        logging.info(result_capture.stdout)
        logging.info(result_capture.stderr)

        return result_capture.stdout  # 返回标准输出
    except Exception as e:
        logging.info(f"Error: {e}")
        return None

def remove_files(file_path):
    command = 'rm -rf ' + file_path
    try:
        # Execute the command
        subprocess.run(command, shell=True, check=True)
        print("Files removed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error occurred: {e}")
    
class LoadAI:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.pic_dir = config.get('path', "/mnt/UDISK/ai_image/flowdetect_img")
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            "LOAD_AI_NOZZLE_CAM_POWER_ON", self.cmd_LOAD_AI_NOZZLE_CAM_POWER_ON)
        self.gcode.register_command(
            "LOAD_AI_NOZZLE_CAM_POWER_OFF", self.cmd_LOAD_AI_NOZZLE_CAM_POWER_OFF)
        self.gcode.register_command(
            "LOAD_AI_SET_AI_SWITCH", self.cmd_LOAD_AI_SET_AI_SWITCH)
        self.gcode.register_command(
            "LOAD_AI_DEAL", self.cmd_LOAD_AI_DEAL)
        self.gcode.register_command(
            "LOAD_AI_DETECT_WASTE", self.cmd_LOAD_AI_DETECT_WASTE)
        self.gcode.register_command(
            "LOAD_AI_GET_STATUS", self.cmd_LOAD_AI_GET_STATUS)
        
        # user_print_refer = "/mnt/UDISK/creality/userdata/config/user_print_refer.json"
        # self.ai_switch = extract_AI_switch_value(user_print_refer)
        # self.cx_ai_engine_status = {
        #     "ai_switch": self.ai_switch,
        #     "command_type": "",
        #     "command": "",
        #     "command_description": "",
        #     "stderr": "",
        #     "ai_results": ""
        # }
        self.cx_ai_engine_status = {}
        self.ai_switch = 0
        self.result = ""
        self.stderr = ""

    def process_waste_ai_detect_result(self, result_stdout_str):
        cnt_pattern = r"ai detection completed, cnt = (\d+)"
        result_pattern = r"(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
        
        # 获取AI识别个数
        cnt_match = re.search(cnt_pattern, result_stdout_str)
        if cnt_match:
            ai_size_int = int(cnt_match.group(1))
            ai_results = []

            logging.info("ai_size_int:%d", ai_size_int)
            # 提取检测结果
            data_start = result_stdout_str.split("num / re_label / re_prob / re_obj_rect_x / re_obj_rect_y / re_obj_rect_width / re_obj_rect_height")[-1]
            data_start = data_start.strip().splitlines()

            for line in data_start:
                match = re.match(result_pattern, line)
                if match:
                    ai_result = {
                        "num": int(match.group(1)),
                        "re_label": int(match.group(2)),
                        "re_prob": float(match.group(3)),
                        "re_obj_rect_x": float(match.group(4)),
                        "re_obj_rect_y": float(match.group(5)),
                        "re_obj_rect_width": float(match.group(6)),
                        "re_obj_rect_height": float(match.group(7))
                    }

                     # 移除不需要的键
                    del ai_result["re_obj_rect_x"]
                    del ai_result["re_obj_rect_y"]
                    del ai_result["re_obj_rect_width"]
                    del ai_result["re_obj_rect_height"]

                    re_prob = ai_result["re_prob"]
                    # ai_results.append(re_prob)
                    ai_results.append(ai_result)
            
            ai_results = json.dumps(ai_results)
            return ai_results

        return None

    def cmd_LOAD_AI_NOZZLE_CAM_POWER_ON(self, gcmd):
        nozzle_cam_power_on()
        
    def cmd_LOAD_AI_NOZZLE_CAM_POWER_OFF(self, gcmd):
        nozzle_cam_power_off()

    def cmd_LOAD_AI_SET_AI_SWITCH(self, gcmd):
        logging.info("gcmd: %s"% gcmd.get_command_parameters())
        self.ai_switch = gcmd.get_int("VALUE", minval=0, maxval=1)
        logging.info("ai_switch: %d" % self.ai_switch)
        # user_print_refer = "/mnt/UDISK/creality/userdata/config/user_print_refer.json"
        # self.ai_switch = extract_AI_switch_value(user_print_refer)
        self.cx_ai_engine_status = {
            "ai_switch": self.ai_switch,
            "command_type": "",
            "command": "",
            "command_description": "",
            "stderr": "",
            "ai_results": ""
        }
        logging.info("LOAD_AI_SET_AI_SWITCH:%s" % self.cx_ai_engine_status)

    def cmd_LOAD_AI_DEAL(self, gcmd):
        # 加载AI上传图片
        try:
            # ip = self.get_ip()
            # if not ip:
            #     self.gcode.respond_info("LOAD_AI_DEAL net error")
            #     return
            # nozzle_cam_power_on()
            # self.reactor.pause(self.reactor.monotonic() + 1)
            ai_capture()
            self.reactor.pause(self.reactor.monotonic() + 2)
            filename = self.find_latest_photo()
            if not filename or not os.path.exists(filename):
                # 关灯
                # nozzle_cam_power_off()
                self.gcode.respond_info("LOAD_AI_DEAL photo error, filename is %s" % filename)
                return
            files = {'file': filename}
            response = self.send_post_request(files)
            logging.info("LOAD_AI_DEAL:%s" % response)
            self.gcode.respond_info("LOAD_AI_DEAL:%s" % response)
            logging.info("files:%s",files)
            remove_files(filename)
        except Exception as e:
            logging.exception(e)
        # 关灯
        # nozzle_cam_power_off()
    def ai_engine_capture(self, cmd):
        logging.info(f"Executing command: {cmd}")
        try:
            # 运行命令，捕获标准输出和标准错误
            process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            self.result, self.stderr = process.communicate()

            # 检查返回码
            if process.returncode != 0:
                logging.error(f"Command '{cmd}' failed with return code {process.returncode}")
                if self.stderr:
                    logging.error(f"Error output: {self.stderr.strip()}")
                else:
                    logging.error("No error output captured.")

            logging.info(f"Command '{cmd}' returned output: {self.result.strip()}")
        except subprocess.CalledProcessError as e:
            logging.error(f"Command '{e.cmd}' returned non-zero exit status {e.returncode}")
            logging.error(f"Error output: {e.stderr.strip() if e.stderr else 'No error output captured.'}")
        except Exception as e:
            logging.error(f"An unexpected error occurred: {str(e)}")

    # AI 废料槽检测
    def cmd_LOAD_AI_DETECT_WASTE(self,gcmd):  
        # user_print_refer = "/mnt/UDISK/creality/userdata/config/user_print_refer.json"
        # ai_switch = extract_AI_switch_value(user_print_refer)
        # # self.gcode.respond_info(f"switch: {ai_switch}")
        # if ai_switch != 1:
        #     # self.gcode.respond_info(f"switch: {ai_switch}")
        #     return
        
        cmd = "ai_engine 1 5 --user_data_dir=/mnt/UDISK"
        json_output = {
            "ai_switch": self.ai_switch,
            "command_type": "ai_engine",
            "command": cmd,
            "command_description": "waste",
            "stderr": "",
            "ai_results": []
        }

        try:
            self.result = {}
            self.stderr = ""
            # 启动后台线程执行命令
            background_thread = threading.Thread(target=self.ai_engine_capture, args=(cmd,))
            background_thread.start()

            # 等待结果
            for _ in range(100):
                if self.result:
                    break
                self.reactor.pause(self.reactor.monotonic() + 0.1)
            else:
                logging.info("run cmd_LOAD_AI_DETECT_WASTE failed: timeout")
                return
            
            # 处理结果
            if self.stderr:
                json_output["stderr"] = self.stderr
            else:
                ai_results = self.process_waste_ai_detect_result(self.result)
                if ai_results is not None:
                    json_output["ai_results"] = ai_results

             # 更新状态并记录信息
            self.cx_ai_engine_status = copy.deepcopy(json_output)
            json_output["stdout"] = self.result
            json_output_str = json.dumps(json_output, indent=4)
            logging.info(json_output_str)

            # 打印 ai_capture 的输出（可选）
            # self.gcode.respond_info(json_output_str)
            return self.result  # 返回标准输出
        except Exception as e:
            json_output["stderr"] = str(e)
            self.cx_ai_engine_status = json_output
            json_output_str = json.dumps(json_output, indent=4)
            logging.info(json_output_str)
            # self.gcode.respond_info(json_output_str)
            return None

    def create_multipart_form_data(self, files):
        """
        创建并返回multipart/form-data的字节串，用于HTTP POST请求体。
        """
        boundary = '---------------------------' + os.urandom(16).hex()
        body = []

        for name, filepath in files.items():
            with open(filepath, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encode_base64(part)

                # 添加头部信息（作为字节串列表）
                # waste_ip_时间戳
                # upload_new_filename = "waste_" + ip + "_" + os.path.basename(filepath)
                part_headers = [
                    f'Content-Disposition: form-data; name="{name}"; filename="{os.path.basename(filepath)}"',
                    # f'Content-Disposition: form-data; name="{name}"; filename="{upload_new_filename}"',
                    f'Content-Type: {part.get_content_type()}',
                    f'Content-Transfer-Encoding: base64'
                ]
                headers_bytes = [header.encode('utf-8') + b'\r\n' for header in part_headers]

                # 添加到body中
                body.append(f'--{boundary}\r\n'.encode('utf-8'))
                body.extend(headers_bytes)
                body.append(b'\r\n')
                # 添加base64编码的内容（已经是字节串）
                body.append(part.get_payload(decode=True))
                body.append(b'\r\n')

                # 添加最后的边界（带有两个破折号）
        body.append(f'--{boundary}--\r\n'.encode('utf-8'))

        # 将所有部分连接成一个字节串
        return b''.join(body), boundary

    def send_post_request(self, files):
        # 假设URL格式是 http://hostname/path
        # hostname, path = url.split('/', 2)[2], '/' + '/'.join(url.split('/')[3:])
        hostname, path = "http://172.23.88.101:38765", "upload/"
        # 创建multipart/form-data体和边界
        body, boundary = self.create_multipart_form_data(files)

        # 创建HTTP连接并发送请求
        conn = http.client.HTTPConnection("172.23.88.101", 38765)
        headers = {
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body))  # 设置内容长度
        }
        conn.request('POST', path, body, headers)

        # 获取响应
        response = conn.getresponse()
        print(f'Status: {response.status}, Reason: {response.reason}')
        resp_text = response.read().decode('utf-8')
        print(resp_text)  # 假设响应是文本

        # 关闭连接
        conn.close()
        return resp_text

    def find_latest_photo(self):
        """
        查找指定目录中最新的照片文件。

        :param directory: 包含照片的目录路径
        :return: 最新照片文件的完整路径，如果没有找到照片则返回None
        """
        latest_photo_path = None
        latest_photo_mtime = None

        # 遍历目录中的所有文件和文件夹
        for root, dirs, files in os.walk(self.pic_dir):
            for file in files:
                # 检查文件扩展名，以确定它是否是图片
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                    file_path = os.path.join(root, file)
                    mtime = os.path.getmtime(file_path)

                    # 如果这是第一个找到的图片，或者比当前已知的最新图片更新
                    if latest_photo_mtime is None or mtime > latest_photo_mtime:
                        latest_photo_path = file_path
                        latest_photo_mtime = mtime

        return latest_photo_path
    
    def cmd_LOAD_AI_GET_STATUS(self,gcmd):  
        # 接口功能测试
        self.cx_ai_engine_status = {
                "ai_switch": 1,
                "command_type": "ai_engine",
                "command": "ai_engine 1 5 --user_data_dir=/mnt/UDISK",
                "command_description": "waste",
                "stderr": "",
                "ai_results": "cam_type=1\nmode=5\ndebug=0\nuser_data_dir=/mnt/UDISK\ngcode_path=\nz_height=0.000000\nParseParamFile model_str_=F008\nParseParamFile sys_version_=1.1.0.15\nthe pid is alive...!\nflag = 0\ninput = /mnt/UDISK/ai_image/sub_capture.bmp\nAI_upload_mode = 1\n{\"reqId\":\"1722419562737\",\"dn\":\"00000000000000\",\"code\":\"key609\",\"data\":\"0.000000|1722419562.736825|/usr/data/ai_image/ai_property/F008-waste-2024_7_31_17_52_42.jpg\\n\"}\noutput = /mnt/UDISK/ai_image/sub_processed_ai_waste_mode.jpg\nai detection completed, cnt = 2\nnum / re_label / re_prob / re_obj_rect_x / re_obj_rect_y / re_obj_rect_width / re_obj_rect_height\n0\t0\t0.931467\t93.759918\t16.903725\t1009.506836\t1182.096313\n1\t0\t0.831467\t93.759918\t16.903725\t1009.506836\t1182.096313",
        }
        result_stdout = self.cx_ai_engine_status["ai_results"]
        ai_results = self.process_waste_ai_detect_result(result_stdout)
        if ai_results is not None:
            self.cx_ai_engine_status["ai_results"] = ai_results
        json_output_str = json.dumps(self.cx_ai_engine_status, indent=4)  
        logging.info(json_output_str)
        
    def get_status(self, eventtime):
        # user_print_refer = "/mnt/UDISK/creality/userdata/config/user_print_refer.json"
        # ai_switch = extract_AI_switch_value(user_print_refer)
        # self.cx_ai_engine_status["ai_switch"] = ai_switch
        return self.cx_ai_engine_status

def load_config(config):
    return LoadAI(config)