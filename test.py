import pymongo
from pymongo.errors import ConnectionFailure, OperationFailure

# ------------------------------------------------------------------
# 1. MongoDB 연결 정보
# ------------------------------------------------------------------
MONGO_URL = "mongodb://realcatcha:realcatcha@mongodb-0.realcatcha.com:27017,mongodb-1.realcatcha.com:27017/?replicaSet=rs0&readPreference=primary&retryWrites=true&w=majority&authSource=admin"
MONGO_DB = "real"
MONGO_COLLECTION = "basic_label" # 요청하신 컬렉션 이름

def count_by_correct_cells():
    """
    MongoDB에 연결하여 'correct_cells'의 개수별로 문서 수를 집계합니다.
    """
    client = None
    try:
        # 2. MongoDB 클라이언트 생성 및 연결
        client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        client.admin.command('ismaster') # 연결 상태 확인
        db = client[MONGO_DB]
        collection = db[MONGO_COLLECTION]

        # 3. 집계(Aggregation) 파이프라인 정의
        pipeline = [
            {
                '$project': {
                    'cells_count': {
                        '$cond': {
                            # 'correct_cells' 필드가 비어있거나 존재하지 않으면 0으로 처리
                            'if': {'$and': [{'$ne': ['$correct_cells', None]}, {'$ne': ['$correct_cells', '']}]},
                            # 필드가 존재하면 콤마(,)를 기준으로 나눈 배열의 크기(개수)를 계산
                            'then': {'$size': {'$split': ['$correct_cells', ',']}},
                            'else': 0
                        }
                    }
                }
            },
            {
                '$group': {
                    # 'cells_count'를 기준으로 그룹화하고, 각 그룹의 문서 수를 합산
                    '_id': '$cells_count',
                    'document_count': {'$sum': 1}
                }
            },
            {
                # 결과를 개수(_id) 기준으로 오름차순 정렬
                '$sort': {'_id': 1}
            }
        ]

        # 4. 집계 실행
        results = list(collection.aggregate(pipeline))

        # 5. 결과 출력
        print(f"'{MONGO_COLLECTION}' 컬렉션의 'correct_cells' 개수별 문서 수:")
        print("----------------------------------------")
        if not results:
            print("  - 집계된 데이터가 없습니다.")
        else:
            for doc in results:
                print(f"  - correct_cells 개수: {doc['_id']}, 문서 수: {doc['document_count']}")
        print("----------------------------------------")

    except ConnectionFailure:
        print(f"MongoDB 연결에 실패했습니다. 연결 정보(URL)를 확인해주세요.")
    except OperationFailure:
        print(f"MongoDB 작업에 실패했습니다. 권한 또는 컬렉션 이름을 확인해주세요.")
    except Exception as e:
        print(f"알 수 없는 오류가 발생했습니다: {e}")
    finally:
        if client:
            client.close()

# 함수 실행
if __name__ == "__main__":
    count_by_correct_cells()