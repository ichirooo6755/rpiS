#!/usr/bin/env python3
"""
Wi-Fi Mode Manager for Raspberry Pi
AP Mode (Pi broadcasts Wi-Fi) / Tethering Mode (Pi connects to phone's Wi-Fi) を切り替える
"""

import os
import subprocess
import json
import logging
import time

logger = logging.getLogger(__name__)

# 設定ファイルパス
SETTINGS_FILE = '/home/pi/camera_settings.json'
HOSTAPD_CONF = '/etc/hostapd/hostapd.conf'
DNSMASQ_CONF = '/etc/dnsmasq.d/picamera.conf'
WPA_SUPPLICANT_CONF = '/etc/wpa_supplicant/wpa_supplicant.conf'

# APモード時の固定IP
AP_IP = '192.168.4.1'
AP_SUBNET = '192.168.4.0/24'
AP_DHCP_RANGE_START = '192.168.4.2'
AP_DHCP_RANGE_END = '192.168.4.20'


def get_current_mode():
    """
    現在のWi-Fiモードを取得する
    Returns: 'ap' or 'tethering'
    """
    try:
        # hostapd が動いているかどうかで判定
        result = subprocess.run(
            ['systemctl', 'is-active', 'hostapd'],
            capture_output=True, text=True
        )
        if result.stdout.strip() == 'active':
            return 'ap'
        return 'tethering'
    except Exception as e:
        logger.error(f"Failed to get Wi-Fi mode: {e}")
        return 'tethering'


def get_wifi_status():
    """
    Wi-Fiの詳細ステータスを取得
    """
    mode = get_current_mode()
    
    status = {
        'mode': mode,
        'ip_address': None,
        'ssid': None
    }
    
    try:
        # IPアドレスを取得
        result = subprocess.run(
            ['hostname', '-I'],
            capture_output=True, text=True
        )
        ips = result.stdout.strip().split()
        if ips:
            status['ip_address'] = ips[0]
        
        if mode == 'ap':
            # APモード時はhostapd設定からSSIDを取得
            if os.path.exists(HOSTAPD_CONF):
                with open(HOSTAPD_CONF, 'r') as f:
                    for line in f:
                        if line.startswith('ssid='):
                            status['ssid'] = line.strip().split('=', 1)[1]
                            break
        else:
            # テザリングモード時は接続中のSSIDを取得
            result = subprocess.run(
                ['iwgetid', '-r'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                status['ssid'] = result.stdout.strip()
                
    except Exception as e:
        logger.error(f"Failed to get Wi-Fi status: {e}")
    
    return status


def switch_to_ap_mode(ssid='PiCamera', password='picamera123'):
    """
    APモードに切り替える
    Args:
        ssid: アクセスポイントのSSID
        password: WPA2パスワード（8文字以上）
    Returns:
        dict: {'success': bool, 'message': str}
    """
    try:
        logger.info(f"Switching to AP mode: SSID={ssid}")
        
        # パスワード長チェック
        if len(password) < 8:
            return {'success': False, 'message': 'パスワードは8文字以上必要です'}
        
        # 1. hostapd設定ファイルを作成
        hostapd_config = f"""interface=wlan0
driver=nl80211
ssid={ssid}
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
"""
        
        # sudo で書き込み
        process = subprocess.run(
            ['sudo', 'tee', HOSTAPD_CONF],
            input=hostapd_config,
            capture_output=True, text=True
        )
        if process.returncode != 0:
            return {'success': False, 'message': f'hostapd設定の書き込みに失敗: {process.stderr}'}
        
        # 2. dnsmasq設定ファイルを作成
        dnsmasq_config = f"""interface=wlan0
dhcp-range={AP_DHCP_RANGE_START},{AP_DHCP_RANGE_END},255.255.255.0,24h
"""
        
        process = subprocess.run(
            ['sudo', 'tee', DNSMASQ_CONF],
            input=dnsmasq_config,
            capture_output=True, text=True
        )
        if process.returncode != 0:
            return {'success': False, 'message': f'dnsmasq設定の書き込みに失敗: {process.stderr}'}
        
        # 3. dhcpcd と wpa_supplicant を停止（これで家のルーターからIPを貰わなくなる）
        subprocess.run(['sudo', 'systemctl', 'stop', 'dhcpcd'], capture_output=True)
        subprocess.run(['sudo', 'systemctl', 'stop', 'wpa_supplicant'], capture_output=True)
        
        # 4. wlan0に固定IPを設定
        subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', 'wlan0'], capture_output=True)
        subprocess.run(['sudo', 'ip', 'addr', 'add', f'{AP_IP}/24', 'dev', 'wlan0'], capture_output=True)
        subprocess.run(['sudo', 'ip', 'link', 'set', 'wlan0', 'up'], capture_output=True)
        
        # 5. hostapd と dnsmasq を起動
        subprocess.run(['sudo', 'systemctl', 'unmask', 'hostapd'], capture_output=True)
        result = subprocess.run(['sudo', 'systemctl', 'start', 'hostapd'], capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"hostapd start failed: {result.stderr}")
            return {'success': False, 'message': f'hostapd起動に失敗: {result.stderr}'}
        
        result = subprocess.run(['sudo', 'systemctl', 'start', 'dnsmasq'], capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"dnsmasq start failed: {result.stderr}")
            return {'success': False, 'message': f'dnsmasq起動に失敗: {result.stderr}'}
        
        # 設定を保存
        _save_wifi_settings('ap', ssid, password)
        
        logger.info("Successfully switched to AP mode")
        return {
            'success': True, 
            'message': f'APモードに切り替えました。スマホで「{ssid}」に接続してください。',
            'ip': AP_IP
        }
        
    except Exception as e:
        logger.error(f"AP mode switch failed: {e}")
        return {'success': False, 'message': str(e)}


def switch_to_tethering_mode():
    """
    テザリングモード（通常のWi-Fiクライアントモード）に切り替える
    Returns:
        dict: {'success': bool, 'message': str}
    """
    try:
        logger.info("Switching to tethering mode")
        
        # 1. hostapd と dnsmasq を停止
        subprocess.run(['sudo', 'systemctl', 'stop', 'hostapd'], capture_output=True)
        subprocess.run(['sudo', 'systemctl', 'stop', 'dnsmasq'], capture_output=True)
        
        # 2. wlan0のIPをクリア
        subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', 'wlan0'], capture_output=True)
        
        # 3. wpa_supplicantを再起動（テザリングWi-Fiに接続）
        subprocess.run(['sudo', 'systemctl', 'start', 'wpa_supplicant'], capture_output=True)
        
        # 4. dhcpcdを再起動してIPを取得
        subprocess.run(['sudo', 'systemctl', 'restart', 'dhcpcd'], capture_output=True)
        
        # 5. 接続を待つ
        time.sleep(5)
        
        # 設定を保存
        _save_wifi_settings('tethering', None, None)
        
        logger.info("Successfully switched to tethering mode")
        return {
            'success': True,
            'message': 'テザリングモードに切り替えました。スマホのテザリングに接続中...'
        }
        
    except Exception as e:
        logger.error(f"Tethering mode switch failed: {e}")
        return {'success': False, 'message': str(e)}


def _save_wifi_settings(mode, ssid, password):
    """Wi-Fi設定を保存"""
    try:
        settings = {}
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
        
        settings['wifi_mode'] = mode
        if ssid:
            settings['ap_ssid'] = ssid
        if password:
            settings['ap_password'] = password
        
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
            
    except Exception as e:
        logger.error(f"Failed to save Wi-Fi settings: {e}")


def get_saved_ap_settings():
    """保存されているAP設定を取得"""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
            return {
                'ssid': settings.get('ap_ssid', 'PiCamera'),
                'password': settings.get('ap_password', 'picamera123')
            }
    except Exception:
        pass
    return {'ssid': 'PiCamera', 'password': 'picamera123'}


if __name__ == '__main__':
    # テスト用
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) < 2:
        print(f"Current mode: {get_current_mode()}")
        print(f"Status: {get_wifi_status()}")
    elif sys.argv[1] == 'ap':
        ssid = sys.argv[2] if len(sys.argv) > 2 else 'PiCamera'
        password = sys.argv[3] if len(sys.argv) > 3 else 'picamera123'
        print(switch_to_ap_mode(ssid, password))
    elif sys.argv[1] == 'tethering':
        print(switch_to_tethering_mode())
