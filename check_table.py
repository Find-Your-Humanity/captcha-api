#!/usr/bin/env python3
import os
import pymysql

# 데이터베이스 연결
conn = pymysql.connect(
    host='az-a.team1-db.1bb3c9ceb1db43928600b93b2a2b1d50.mysql.managed-service.kr-central-2.kakaocloud.com',
    port=13306,
    user='realcatcha',
    password='realcatcha',
    database='captcha'
)

try:
    with conn.cursor() as cursor:
        # 테이블 존재 확인
        cursor.execute('SHOW TABLES LIKE "suspicious_ips"')
        result = cursor.fetchone()
        if result:
            print('✅ suspicious_ips 테이블 존재')
            # 테이블 구조 확인
            cursor.execute('DESCRIBE suspicious_ips')
            columns = cursor.fetchall()
            print('📋 테이블 구조:')
            for col in columns:
                print(f'  - {col[0]}: {col[1]}')
        else:
            print('❌ suspicious_ips 테이블이 존재하지 않음')
            
        # ip_violation_stats 테이블 확인
        cursor.execute('SHOW TABLES LIKE "ip_violation_stats"')
        result = cursor.fetchone()
        if result:
            print('✅ ip_violation_stats 테이블 존재')
        else:
            print('❌ ip_violation_stats 테이블이 존재하지 않음')
            
finally:
    conn.close()
