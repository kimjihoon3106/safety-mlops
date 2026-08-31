# Ubuntu Secure Boot 환경에서 NVIDIA GPU를 Docker와 k3s까지 연결하기

## 개요

Ubuntu 22.04 서버에서 NVIDIA 드라이버 패키지는 설치되어 있었지만 `nvidia-smi`가 GPU 드라이버와 통신하지 못했다. 기존 드라이버를 제거하고 `nvidia-driver-595-open`을 다시 설치해도 증상은 동일했다.

조사 결과 첫 번째 원인은 **Secure Boot가 로컬 키로 서명된 DKMS 커널 모듈의 로딩을 차단한 것**이었다. 호스트 GPU를 복구한 뒤에는 Kubernetes의 NVIDIA Device Plugin이 NVML 라이브러리를 찾지 못하는 두 번째 문제가 발생했다. 이 문제는 Docker에 NVIDIA 런타임이 등록만 되어 있고 기본 런타임으로 지정되지 않아 발생했다.

최종적으로 다음 환경을 구성하고 GPU 동작을 검증했다.

- Ubuntu 22.04.5 LTS
- NVIDIA Quadro RTX 5000 16GB
- `nvidia-driver-595-open` 595.84
- Docker 29.7.2
- NVIDIA Container Toolkit 1.20.0
- k3s v1.36.3+k3s1 (`--docker` 런타임)
- NVIDIA Kubernetes Device Plugin v0.20.0

## 1. 장애 증상

서버에는 기존 `nvidia-driver-595-server` 패키지가 설치되어 있었지만 다음 명령이 실패했다.

```bash
nvidia-smi
```

```text
NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.
Make sure that the latest NVIDIA driver is installed and running.
```

PCI 장치 자체는 정상적으로 인식되고 있었다.

```bash
lspci -nn | grep -i nvidia
```

따라서 하드웨어 미인식보다는 사용자 공간 드라이버와 커널 모듈 사이의 문제를 우선 의심했다.

## 2. 기존 드라이버 제거 및 Open 드라이버 재설치

기존 NVIDIA 패키지를 제거하고 불필요한 의존성을 정리했다.

```bash
sudo apt-get purge -y '*nvidia*'
sudo apt-get autoremove -y
sudo apt-get update
```

요청한 Open Kernel Module 기반 드라이버를 설치했다.

```bash
sudo apt-get install -y nvidia-driver-595-open
```

설치 상태와 DKMS 빌드 결과는 정상이었다.

```bash
dpkg -s nvidia-driver-595-open | grep -E '^(Status|Version):'
dkms status
```

```text
Status: install ok installed
Version: 595.84-0ubuntu0.22.04.1
nvidia/595.84, 5.15.0-171-generic, x86_64: installed
```

하지만 재부팅 후에도 `nvidia-smi`는 계속 실패했다. 즉, **패키지 설치 성공과 커널 모듈 로딩 성공은 별개의 문제**였다.

## 3. 원인 분석: Secure Boot의 모듈 로딩 차단

먼저 NVIDIA 모듈의 로딩 여부와 Secure Boot 상태를 확인했다.

```bash
lsmod | grep -E 'nvidia|nouveau'
mokutil --sb-state
sudo modprobe nvidia
sudo dmesg -T | grep -Ei 'nvidia|secure|lockdown|verification'
```

확인된 핵심 메시지는 다음과 같았다.

```text
SecureBoot enabled
modprobe: ERROR: could not insert 'nvidia': Operation not permitted
Lockdown: unsigned module loading is restricted
```

DKMS가 생성한 모듈 파일에는 로컬 키 서명이 존재했다.

```bash
modinfo nvidia | grep -E '^(version|signer):'
```

그러나 해당 키가 시스템의 MOK(Machine Owner Key)에 등록되어 있지 않았다. Secure Boot가 활성화된 Ubuntu 커널은 신뢰하지 않는 키로 서명된 모듈을 로드하지 않기 때문에 `nvidia-smi`가 드라이버와 통신할 수 없었다.

정리하면 장애 흐름은 다음과 같다.

```text
드라이버 패키지 설치 성공
→ DKMS 모듈 빌드 성공
→ 로컬 키로 모듈 서명
→ 해당 키가 Secure Boot 신뢰 체인에 없음
→ 커널이 NVIDIA 모듈 로딩 차단
→ nvidia-smi 실패
```

## 4. 해결: Canonical 서명 NVIDIA 모듈 사용

Secure Boot를 비활성화하거나 MOK를 수동 등록할 수도 있지만, 원격 서버에서 펌웨어/MOK 화면을 조작해야 하는 부담이 있다. Secure Boot를 유지하면서 해결하기 위해 Ubuntu가 제공하는 Canonical 서명 모듈을 설치했다.

```bash
sudo apt-get install -y linux-modules-nvidia-595-open-generic
sudo reboot
```

이 메타 패키지는 당시 최신 5.15 generic 커널과 해당 커널용 NVIDIA Open 모듈을 함께 설치했다.

```text
linux-image-5.15.0-190-generic
linux-modules-nvidia-595-open-5.15.0-190-generic
```

재부팅 후 커널과 모듈 서명을 확인했다.

```bash
uname -r
modinfo nvidia | grep -E '^(version|signer):'
```

```text
5.15.0-190-generic
version:        595.84
signer:         Canonical Ltd. Kernel Module Signing
```

이후 호스트의 `nvidia-smi`가 정상적으로 실행되었으며 Quadro RTX 5000과 16GB GPU 메모리가 확인됐다.

## 5. Docker 및 NVIDIA Container Toolkit 구성

Docker를 설치하고 서비스를 활성화했다.

```bash
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sudo sh /tmp/get-docker.sh
sudo systemctl enable --now docker
```

NVIDIA Container Toolkit 공식 저장소를 등록했다.

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
```

Docker에 NVIDIA 런타임을 등록하고 Docker를 재시작했다.

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Docker 컨테이너에서 GPU가 전달되는지 확인했다.

```bash
sudo docker run --rm --gpus all \
  nvidia/cuda:12.0.0-base-ubuntu22.04 \
  nvidia-smi
```

컨테이너 안에서 Quadro RTX 5000과 드라이버 595.84가 정상적으로 표시됐다.

## 6. k3s 구성

k3s가 Docker를 컨테이너 런타임으로 사용하도록 설치했다.

```bash
curl -sfL https://get.k3s.io | sh -s - --docker
```

일반 사용자 계정에서 `kubectl`을 사용할 수 있도록 kubeconfig를 구성했다.

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u):$(id -g)" ~/.kube/config
chmod 600 ~/.kube/config
```

노드가 정상 상태인지 확인했다.

```bash
kubectl get nodes -o wide
```

```text
NAME    STATUS   ROLES           VERSION
gsmsv   Ready    control-plane   v1.36.3+k3s1
```

## 7. Kubernetes Device Plugin의 NVML 오류

Kubernetes가 GPU를 스케줄링 자원으로 등록하도록 NVIDIA Device Plugin을 배포했다.

```bash
kubectl apply -f \
  https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.20.0/deployments/static/nvidia-device-plugin.yml
```

DaemonSet은 `Running`이었지만 노드의 `nvidia.com/gpu` capacity가 나타나지 않았다.

```bash
kubectl -n kube-system logs daemonset/nvidia-device-plugin-daemonset
```

로그에는 다음 오류가 기록되어 있었다.

```text
Failed to initialize NVML: ERROR_LIBRARY_NOT_FOUND
If this is a GPU node, did you set the docker default runtime to `nvidia`?
```

`/etc/docker/daemon.json`을 확인해 보니 `nvidia` 런타임은 등록되어 있었지만 Docker의 기본 런타임은 아니었다.

```json
{
  "runtimes": {
    "nvidia": {
      "args": [],
      "path": "nvidia-container-runtime"
    }
  }
}
```

일반 Docker 테스트에서는 `--gpus all` 옵션이 NVIDIA 런타임 연결을 처리하지만, k3s가 생성하는 Device Plugin 컨테이너에는 동일한 방식이 자동 적용되지 않았다. 그 결과 플러그인 컨테이너가 호스트의 NVML 라이브러리에 접근하지 못했다.

## 8. 해결: NVIDIA를 Docker 기본 런타임으로 설정

NVIDIA 런타임을 Docker의 기본 런타임으로 지정했다.

```bash
sudo nvidia-ctk runtime configure \
  --runtime=docker \
  --set-as-default

sudo systemctl restart docker
sudo systemctl restart k3s
```

재시작 후 Device Plugin이 GPU를 감지했고 노드에 GPU 자원이 등록됐다.

```bash
kubectl get node gsmsv \
  -o custom-columns='NAME:.metadata.name,GPU_CAPACITY:.status.capacity.nvidia\.com/gpu,GPU_ALLOCATABLE:.status.allocatable.nvidia\.com/gpu'
```

```text
NAME    GPU_CAPACITY   GPU_ALLOCATABLE
gsmsv   1              1
```

## 9. 최종 검증

### 호스트

```bash
nvidia-smi
```

- NVIDIA 드라이버 595.84 정상 로딩
- Quadro RTX 5000 정상 인식
- GPU 메모리 16GB 확인

### Docker

```bash
sudo docker run --rm --gpus all \
  nvidia/cuda:12.0.0-base-ubuntu22.04 \
  nvidia-smi
```

- CUDA 컨테이너에서 GPU 인식 성공
- NVIDIA Container Toolkit 동작 확인

### Kubernetes

GPU 1개를 요청하는 테스트 파드를 생성했다.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: gpu-verification
spec:
  restartPolicy: Never
  containers:
    - name: gpu-verification
      image: nvidia/cuda:12.0.0-base-ubuntu22.04
      command: ["nvidia-smi"]
      resources:
        limits:
          nvidia.com/gpu: 1
```

테스트 파드 로그에서도 Quadro RTX 5000과 드라이버 595.84가 정상적으로 출력됐다. 테스트가 끝난 뒤 파드는 삭제했다.

## 10. 배운 점

### 패키지가 설치됐다고 드라이버가 동작하는 것은 아니다

`dpkg`와 DKMS의 성공 여부만 확인하면 커널 모듈 로딩 문제를 놓칠 수 있다. 반드시 다음 항목을 함께 확인해야 한다.

```bash
lsmod | grep nvidia
sudo modprobe nvidia
sudo dmesg -T | grep -Ei 'nvidia|secure|lockdown'
nvidia-smi
```

### Secure Boot 환경에서는 모듈의 신뢰 체인이 중요하다

모듈에 서명이 있다는 사실만으로는 충분하지 않다. 해당 서명키가 Secure Boot의 신뢰 체인에 포함되어 있어야 한다. 가능한 해결책은 다음과 같다.

1. MOK에 로컬 서명키 등록
2. Secure Boot 비활성화
3. 배포판이 공식 서명한 커널 모듈 사용

이번 사례에서는 Secure Boot를 유지할 수 있고 원격 작업에도 적합한 세 번째 방법을 선택했다.

### Docker GPU 동작과 Kubernetes GPU 인식은 별개다

Docker에서 `--gpus all` 테스트가 성공해도 Kubernetes가 자동으로 GPU를 스케줄링할 수 있는 것은 아니다. Kubernetes에서는 다음 조건이 추가로 필요하다.

- NVIDIA Device Plugin 또는 GPU Operator
- 노드 런타임에서 NVIDIA 라이브러리에 접근 가능한 설정
- 노드의 `nvidia.com/gpu` capacity/allocatable 등록
- GPU limit을 요청한 실제 파드 검증

## 장애 해결 흐름 요약

```text
nvidia-smi 실패
→ PCI 장치는 정상 인식
→ NVIDIA 패키지 제거 및 595-open 재설치
→ 재부팅 후에도 실패
→ modprobe와 dmesg에서 Secure Boot 차단 확인
→ Canonical 서명 커널 모듈 설치
→ 호스트 nvidia-smi 정상화
→ Docker GPU 테스트 성공
→ k3s Device Plugin에서 NVML 오류 발견
→ NVIDIA를 Docker 기본 런타임으로 설정
→ Kubernetes GPU capacity 1 등록
→ GPU 요청 파드에서 nvidia-smi 최종 성공
```

## 11. YOLOv8 학습 및 TensorRT 변환 트러블슈팅

### 증상

YOLOv8n 5 epoch 학습과 ONNX export까지는 완료됐지만, TensorRT Python 패키지 설치 중 루트 디스크 공간이 부족해 `best.engine` 생성이 중단됐다. 당시 서버의 루트 디스크는 20GB였고, PyTorch/CUDA가 포함된 학습용 가상환경이 약 6.4GB를 사용하고 있었다. MLflow 로컬 이미지도 디스크 정리 과정에서 제거되어 파드가 `ErrImageNeverPull` 상태가 됐다.

### 해결

- 학습 결과인 `best.pt`와 `best.onnx`가 보존됐는지 먼저 확인했다.
- 학습이 끝난 뒤 더 이상 필요하지 않은 학습용 가상환경만 제거했다.
- TensorRT, ONNX, boto3만 포함하는 별도 경량 가상환경을 생성했다.
- TensorRT 10.13.3.9 Python Builder API로 FP16 엔진을 생성했다.
- MLflow 이미지를 다시 빌드하고 실패 파드를 교체했다.
- 모델 파일을 S3에 직접 업로드한 뒤 MLflow를 통해서도 다시 업로드하여 S3 연동을 종단 검증했다.

RAM 증설 후 확인 결과 시스템 메모리는 7.7GiB로 인식됐다. `nvidia-smi`에 표시된 16GB는 시스템 RAM이 아니라 Quadro RTX 5000의 GPU VRAM이다. 현재 구성에서도 TensorRT 변환은 성공했지만, 의도한 증설량이 시스템 RAM 16GB라면 VM 또는 호스트 할당 설정을 다시 확인해야 한다.

### 변환 결과

```text
ONNX input : images  [batch, 3, height, width]
ONNX output: output0 [batch, 29, anchors]

TensorRT precision: FP16
min profile: [1, 3, 320, 320]
opt profile: [4, 3, 640, 640]
max profile: [8, 3, 640, 640]
```

생성된 파일은 다음과 같다.

```text
best.pt             6,229,411 bytes
best.onnx          12,469,720 bytes
best.engine         8,324,252 bytes
model_metadata.json    1,093 bytes
```

S3 저장 경로:

```text
s3://mlops-safety-323974325951-ap-northeast-2/models/yolov8n-safety-20260828/
```

MLflow 최종 run ID:

```text
b9b91e8f7b5547999260d161c991c1d7
```

최종 확인 시 `mlops` 네임스페이스의 MLflow 파드는 `1/1 Running`, PVC는 `Bound`, MLflow `/health` 응답은 `OK`였다. NVIDIA Device Plugin도 `Running`이며 노드에 `nvidia.com/gpu: 1`이 allocatable로 등록돼 있었다.

## 12. Triton 배포 준비와 디스크 용량 문제

TensorRT 10.13.3.9로 생성한 엔진과 정확히 호환되는 `nvcr.io/nvidia/tritonserver:25.10-py3` 이미지를 선택했다. Triton 모델 저장소와 Kubernetes Deployment/Service, 실제 HTTP 추론 검증 스크립트까지 작성했다.

```text
triton/model-repository/yolov8/config.pbtxt
k8s/triton.yaml
pipeline/test_triton.py
```

서버에는 다음 모델 저장소를 준비했다.

```text
/home/ubuntu/mlops-stack/triton/model-repository/yolov8/
├── config.pbtxt
└── 1/
    └── model.plan
```

하지만 20GB 루트 디스크에서 Triton 공식 이미지를 압축 해제하던 중 `no space left on device`가 발생했다. 실패한 레이어는 정리되어 디스크 여유가 약 12GB로 복구됐지만, 전체 이미지의 설치에는 부족하다. 이 과정에서 발생한 k3s `DiskPressure`도 해제했으며 MLflow는 다시 `1/1 Running`, `/health` 응답 `OK`로 복구했다.

Triton 배포를 계속하려면 루트 디스크를 최소 40GB, 운영 여유를 고려하면 60GB 이상으로 확장하는 것이 적절하다. 디스크 확장 후에는 이미지 pull, Deployment 적용, 모델 readiness 및 실제 inference 요청 검증을 이어서 수행한다.

### 디스크 확장 및 Triton 최종 배포

가상 디스크를 40GB로 증설한 직후에도 `/dev/sda1`은 20GB로 남아 있었다. 가상 디스크 크기 변경만으로 파티션과 파일시스템이 자동 확장되지 않았기 때문이다.

```bash
sudo growpart /dev/sda 1
sudo resize2fs /dev/sda1
```

온라인 확장 후 루트 파일시스템은 39GB로 인식됐고 Triton 25.10 이미지를 정상적으로 설치했다.

최초 Triton 구동에서는 외부 Python TensorRT 환경에서 생성한 엔진을 역직렬화하는 과정에서 다음 오류가 발생했다.

```text
unable to create TensorRT engine
Assertion convShader != nullptr failed
```

TensorRT 엔진은 버전 문자열이 같아도 CUDA/TensorRT 빌드 환경과 GPU에 민감하다. 따라서 Triton 25.10 컨테이너에 포함된 TensorRT 10.13.3 `trtexec`로 ONNX를 다시 변환했다.

```text
precision: FP16
min: images=1x3x320x320
opt: images=4x3x640x640
max: images=8x3x640x640
```

재생성된 엔진은 컨테이너 내부 역직렬화 검증을 통과했고 k3s의 Triton 파드가 `1/1 Running` 상태가 됐다. 실제 HTTP 추론 결과는 다음과 같다.

```text
input : images  FP32 [1, 3, 320, 320]
output: output0 FP32 [1, 29, 2100]
nv_inference_request_success: 1
nv_inference_count: 1
```

최종 배포용 모델 저장소는 S3의 다음 경로에도 업로드했다.

```text
s3://mlops-safety-323974325951-ap-northeast-2/models/yolov8n-safety-20260828/triton/
```

최종 상태는 MLflow와 Triton 모두 `1/1 Running`, GPU 1개 allocatable, 루트 디스크 39GB 중 약 13GB 여유다.

## 13. 실제 이미지 추론 및 FastAPI 연결

더미 텐서 검증 이후 실제 이미지 추론 클라이언트를 구현했다.

```text
pipeline/infer_image.py
```

클라이언트 처리 과정은 다음과 같다.

```text
이미지 로드
→ RGB 변환 및 letterbox resize
→ FP32 NCHW 정규화
→ Triton HTTP 추론
→ confidence 필터
→ 클래스별 NMS
→ 원본 좌표 복원
→ bounding box 이미지와 JSON 저장
```

Roboflow 테스트 이미지에서 실제 GPU 추론을 실행한 결과 굴착기 한 대를 탐지했다.

```text
class: Excavator
confidence: 0.686
box_xyxy: [272.4, 57.0, 1014.6, 670.5]
```

결과 파일은 서버의 다음 경로에 저장된다.

```text
/home/ubuntu/mlops-stack/inference-results/
```

안전장구 라벨이 포함된 다른 테스트 이미지에서는 `Person 0.2817`만 검출하고 안전모와 조끼를 놓쳤다. 추론 연결이나 후처리 오류가 아니라 5 epoch 테스트 모델의 낮은 정확도에 따른 한계이므로 실사용 전 추가 학습이 필요하다.

이미지 업로드 API도 구현하여 k3s에 배포했다.

```text
api/app.py
docker/inference-api/Dockerfile
k8s/inference-api.yaml
```

FastAPI는 `POST /detect`로 이미지를 받고 내부 DNS `triton.mlops.svc.cluster.local:8000`을 통해 Triton에 요청한다. 실제 multipart JPG 업로드 테스트에서도 동일한 탐지 결과를 반환했다.

```text
inference-api  1/1 Running
mlflow         1/1 Running
triton         1/1 Running
```

현재 inference-api는 외부에 공개하지 않고 `ClusterIP`로 유지했다. 인증과 TLS를 구성하기 전 NodePort 또는 인터넷 공개를 피하는 것이 안전하다.

## 14. Roboflow 데이터셋 자동 감지

Roboflow에 새로운 데이터셋 버전이 생성됐는지 6시간마다 확인하는 Kubernetes CronJob을 구축했다.

```text
pipeline/dataset_watcher.py
docker/dataset-watcher/Dockerfile
k8s/dataset-automation.yaml
```

자동화 흐름은 다음과 같다.

```text
Roboflow 최신 버전 조회
→ roboflow-dataset-state ConfigMap과 비교
→ 같은 버전이면 종료
→ 새 버전이면 YOLOv8 ZIP 다운로드
→ 이미지 손상 및 data.yaml 검사
→ 이미지/라벨 대응 관계 검사
→ 클래스 ID 및 normalized box 범위 검사
→ S3 datasets/construction-site-safety/vN/ 업로드
→ training-candidate를 AWAITING_APPROVAL로 생성
```

CronJob 스케줄:

```text
17 */6 * * *
```

Roboflow API Key는 `roboflow-credentials`, AWS Key는 기존 `aws-credentials` Kubernetes Secret을 통해 주입한다. 감시 Job은 전용 ServiceAccount를 사용하고 `mlops` 네임스페이스의 ConfigMap만 읽고 변경할 수 있도록 RBAC 권한을 제한했다.

수동 검증 결과:

```json
{"status":"up-to-date","latest":30,"processed":30}
```

현재 Roboflow 최신 버전은 v30이고 이미 처리한 버전도 v30이므로 중복 다운로드나 학습은 발생하지 않는다. 새 버전 검증이 완료돼도 단일 GPU에서 실행 중인 Triton을 자동으로 중단하지 않도록 학습은 승인 상태에서 대기한다.

상태 확인 명령:

```bash
kubectl get cronjob roboflow-dataset-watcher -n mlops
kubectl get configmap roboflow-dataset-state -n mlops -o yaml
kubectl get configmap training-candidate -n mlops -o yaml
```

## 15. ECR, GitOps 및 두 GPU 학습 준비

실행 환경을 역할별로 독립 배포하기 위해 다음 ECR 저장소를 구성했다.

```text
safety-mlops/inference-api
safety-mlops/dataset-watcher
safety-mlops/mlflow
safety-mlops/trainer
```

모델 아티팩트는 ECR 이미지에 포함하지 않는다. 컨테이너 실행 코드는 ECR, 모델과 데이터셋은 S3, Kubernetes desired state와 Production 모델 버전은 GitHub `kimjihoon3106/safety-mlops`에서 관리한다. GitHub Actions는 장기 Access Key 대신 AWS OIDC로 인증하고, 각 이미지를 `sha-<git commit>` 형식의 immutable 태그로 ECR에 푸시한다.

학습 이미지는 다음 태그로 고정했다.

```text
323974325951.dkr.ecr.ap-northeast-2.amazonaws.com/safety-mlops/trainer:sha-1e1113695d6446f9f2ad75105cafdb1332e5bb8e
```

Argo CD에서 `safety-mlops-platform` Application을 수동 동기화한 결과는 `Synced / Healthy`다. 학습 리소스는 등록됐지만 다음과 같이 중지 상태이므로 승인 없이 학습이 실행되지 않는다.

```text
CronJob: safety-training-template
suspend: true
GPU request/limit: nvidia.com/gpu: 1
epochs: 50
```

두 GPU가 동시에 독립적으로 할당되는지도 검증했다. Triton을 실행한 상태에서 GPU 1개를 요청하는 임시 Pod를 생성한 결과 다음 UUID가 각각 노출됐다.

```text
Triton GPU : GPU-c0b2d031-b2b9-e56c-d7c5-9090d7fcaf51
Training GPU smoke pod: GPU-b284e483-ca93-aabc-57c1-3b91c2bf3a30
Kubernetes GPU capacity/allocatable: 2/2
```

임시 검증 Pod는 확인 직후 삭제했다. 표준 NVIDIA Device Plugin은 단일 노드 안에서 물리 GPU 인덱스 `0/1` 자체를 영구 고정하지 않으므로 재시작 시 UUID 순서는 바뀔 수 있다. 현재 구성의 보장은 Triton과 Training Job이 각각 GPU 리소스 하나를 요청하여 동시에 서로 다른 GPU를 점유한다는 것이다. Production Triton에는 높은 PriorityClass를 적용했고, batch 학습은 수동 승인 후에만 실행한다.

Rollback은 Git의 이전 trainer 태그와 Kubernetes 선언으로 되돌린 뒤 Argo CD를 다시 동기화하는 방식으로 수행한다. S3 모델 버전과 ECR immutable 이미지는 덮어쓰거나 삭제하지 않는다.

## 16. Candidate GPU 평가 단계

학습 완료 상태인 `EVALUATING` Candidate를 Production과 비교하는 평가 단계를 추가했다.

```text
pipeline/evaluate_candidate.py
k8s/evaluation-pipeline.yaml
scripts/evaluate-candidate.sh
```

평가 Job은 Candidate ONNX를 S3에서 내려받고 CUDA Execution Provider로 워밍업 10회와 측정 50회를 실행한다. 학습 지표와 p95 지연시간을 `safety-model-evaluation-policy` ConfigMap의 설정값으로 판정하고 `evaluation_report.json`을 Candidate S3 경로에 저장한다. 통과 상태는 `EVALUATION_PASSED`, 정책 미달은 `EVALUATION_REJECTED`, 실행 오류는 `EVALUATION_ERROR`로 기록한다.

평가 템플릿도 `suspend: true`이므로 Candidate가 없는 현재는 Job이 실행되지 않는다. Argo CD 상태는 `Synced / Healthy`이며 Training과 Evaluation 이미지 모두 `sha-e597ec0545baa2a0b17535c4b54acbc97882a2b2`로 고정했다. 평가 통과는 배포 승인이 아니며 다음 Model Conversion/Promotion 단계 전까지 Production v1은 변경되지 않는다.

## 17. Model Promotion, Smoke Test 및 GitOps Rollback

평가 통과 모델을 TensorRT FP16으로 변환하고 immutable S3 버전으로 승격하는 Model Operator를 배포했다. 이미지는 mutable tag 대신 다음 ECR digest로 고정했다.

```text
323974325951.dkr.ecr.ap-northeast-2.amazonaws.com/safety-mlops/trainer@sha256:8104bcb4d55f6575346b5f2bcdfecbb62df3e6e8b63affbffc316a08e244bbbd
```

초기 런타임 점검에서 Triton 기반 이미지에는 `python` 명령이 없고 `python3`만 존재하는 문제가 발견됐다. Docker CMD를 `python3`로 수정한 뒤 실제 Kubernetes 임시 Pod에서 `boto3`, Kubernetes SDK 및 `/usr/src/tensorrt/bin/trtexec`를 확인해 `final-model-operator-ok`를 검증했다. 임시 Pod는 삭제했다.

Promotion은 `CONVERTING`, `READY_FOR_PROMOTION`, `PROMOTING`, `PROMOTED_PENDING_GIT` 상태를 사용한다. 변환 또는 S3 작업 실패 시 각각 `CONVERSION_ERROR`, `PROMOTION_ERROR`를 기록한다. S3 vN 복사 중 실패하면 해당 실행이 만든 객체를 정리하며, 모든 파일 생성이 성공한 경우에만 `_READY` 마커를 기록한다. 기존 v1은 덮어쓰지 않는다.

GitHub Actions `promote-model.yaml`은 현재 Production 버전을 먼저 비교해 동시 Promotion 충돌을 차단한 뒤 `model-release.yaml`과 Triton/API의 model-version annotation을 함께 변경한다. annotation 변경으로 ConfigMap 값만 바뀌고 Pod가 재시작되지 않는 문제를 방지한다. Rollback도 동일한 Git workflow로 이전 버전을 커밋하고 Argo CD가 반영하도록 구성했다.

Production v1 대상으로 S3의 비민감 테스트 이미지 `smoke-tests/ppe.jpg`를 사용해 Kubernetes Smoke Test Job을 두 번 실행했다.

```text
HTTP status: 200
model_version: v1
detection_count: 5
latency: 2477.05 ms / 재배포 후 1412.52 ms
Triton ready: true
Triton GPU: Quadro RTX 5000
nv_inference_request_success: 1
nv_inference_count: 1
```

최종 상태는 Argo CD `Synced / Healthy`, inference-api·MLflow·Triton `1/1 Running`, GPU capacity/allocatable `2/2`, Production `v1`이다. 실제 신규 Candidate가 없으므로 가짜 v2를 만들거나 Git rollback을 강제로 실행하지 않았다. 실제 Candidate Promotion 시 Smoke Test 실패 경로에서 이전 버전 Git commit과 Argo CD rollback을 최종 실증해야 한다.

## 18. AWS IAM Roles Anywhere 전환

KVM 기반 k3s는 EC2 Instance Profile을 사용할 수 없으므로, 기존 Root 장기 Access Key 대신 X.509 인증서로 임시 STS 자격 증명을 발급하는 IAM Roles Anywhere 경로를 구성했다.

```text
k3s Pod
  -> aws_signing_helper credential_process
  -> IAM Roles Anywhere Trust Anchor / Profile
  -> assumed-role/safety-mlops-k3s
  -> S3 및 ECR 최소 권한 접근
```

AWS에는 `safety-mlops-k3s` Role과 `SafetyMLOpsK3SAccess` inline policy를 생성했다. S3 권한은 단일 MLOps 버킷의 `datasets`, `artifacts`, `models`, `edge-cases`, `smoke-tests` prefix로 제한하고, ECR pull 권한은 프로젝트의 네 저장소로 제한했다. Trust policy는 계정, Trust Anchor ARN, 인증서 Subject CN `safety-mlops-k3s` 조건을 함께 검사한다. GitHub Actions의 AWS OIDC 경로는 변경하지 않았다.

클라이언트 인증서는 EC P-384, 유효기간 90일로 발급했다. CA private key와 인증서 원본은 서버의 root 전용 경로에 보관하고, Kubernetes `aws-roles-anywhere-x509` Secret에는 클라이언트 인증서와 private key만 저장한다. Git에는 인증서와 key를 넣지 않았다. `roles-anywhere-certificate-monitor` CronJob이 매일 만료일을 검사하며 30일 이하이면 실패한다. 2026-08-31 수동 검사 결과는 90.0일 잔여였다.

다음 workload의 장기 Key 환경변수를 AWS SDK 기본 credential provider chain으로 교체했다.

```text
Dataset Watcher
MLflow
Training Job
Evaluation Job
Model Operator / S3 Promotion
Triton model-sync
ECR imagePullSecret refresher
Smoke Test init container
```

검증 결과:

```text
STS identity: arn:aws:sts::323974325951:assumed-role/safety-mlops-k3s/<session>
S3 datasets/artifacts/models/edge-cases: 임시 객체 put/head/delete 성공
MLflow: validation.txt를 artifacts/mlflow/... 경로에 기록 성공
Triton model-sync: model.plan checksum 검증 성공
Dataset Watcher: Roboflow v30 상태 조회 성공
ECR refresher: ecr-registry Secret 교체 성공
Temporary credential: 15분 세션 만료 전 자동 rotation 확인
Production smoke: HTTP 200, model v1, detection 5건
```

MLflow 베이스 이미지의 구형 glibc와 공식 helper 바이너리가 호환되지 않는 문제가 있었다. Ubuntu 20.04(glibc 2.31) 빌더에서 `aws_signing_helper` v1.8.5를 CGO로 컴파일해 MLflow 이미지에 포함했고, 해당 Pod 안에서 STS Role identity와 S3 artifact 기록을 확인했다.

Training/Evaluation/Model Operator용 대형 CUDA trainer 이미지는 인증 검증용 Pod에서도 압축 해제 중 노드 ephemeral storage 임계값에 도달했다. 따라서 이 세 workload의 실제 이미지 기반 재검증과 기존 장기 credential 삭제는 보류한다. 현재 `aws-credentials` Secret과 서버 장기 credential은 fallback 용도로 남아 있지만 새 workload manifest에서는 참조하지 않는다. 디스크를 추가 확보한 뒤 세 Job의 S3 접근을 검증하고, 마지막으로 Kubernetes Secret과 `~/.aws/credentials`를 제거해야 한다. Root Access Key 자체는 AWS Root 계정의 Security Credentials 화면에서 사용자가 최종 삭제해야 한다.
