pipeline {
  agent any
  environment {
    DOCKER_IMAGE = "tecnogera/n1agent"
    TAG         = "${env.BUILD_NUMBER}"
    HOST        = "10.246.200.14"
    REMOTE_DIR  = "/opt/apps/n1agent"
    PORT_BIND   = "127.0.0.1:8001:8001"
  }

  stages {
    stage('Checkout') {
      steps { checkout scm } // usa credencial do SCM configurada no Job (PAT como Username/Password)
    }

    stage('Tests (backend)') {
      steps {
        sh '''
          set -euxo pipefail
          python3 -m venv .venv-ci
          . .venv-ci/bin/activate
          pip install --upgrade pip
          pip install -r requirements.txt
          python -m unittest
        '''
      }
    }

    stage('Frontend Build') {
      steps {
        dir('frontend') {
          sh '''
            set -euxo pipefail
            if [ -f package-lock.json ]; then
              npm ci
            else
              npm install
            fi
            npm run build
          '''
        }
      }
    }

    stage('Build image') {
      steps {
        sh '''
          set -euxo pipefail
          docker build -t ${DOCKER_IMAGE}:${TAG} -t ${DOCKER_IMAGE}:latest .
        '''
      }
    }

    stage('Push image') {
      steps {
        withCredentials([usernamePassword(credentialsId: 'dockerhub-creds', usernameVariable: 'DH_USER', passwordVariable: 'DH_PASS')]) {
          sh '''
            set -euxo pipefail
            echo "${DH_PASS}" | docker login -u "${DH_USER}" --password-stdin
            docker push ${DOCKER_IMAGE}:${TAG}
            docker push ${DOCKER_IMAGE}:latest
          '''
        }
      }
    }

    stage('Deploy (compose up)') {
      steps {
        // 1) gera docker-compose no workspace (sem segredos; usa env_file no host)
        writeFile file: 'docker-compose.deploy.yml', text: """services:
  n1agent:
    image: ${DOCKER_IMAGE}:${TAG}
    env_file: .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8001/healthz || exit 1"]
      interval: 20s
      timeout: 5s
      retries: 5
    ports:
      - "${PORT_BIND}"
"""

        // 2) gera script remoto de deploy
        writeFile file: 'remote_deploy.sh', text: '''#!/usr/bin/env bash
set -euxo pipefail

: "${REMOTE_DIR:?REMOTE_DIR não definido}"

cd "$REMOTE_DIR"

# normalizar fim de linha (CRLF -> LF), se houver
sed -i 's/\r$//' docker-compose.yml || true

echo '--- docker-compose.yml ---'
sed -n '1,80p' docker-compose.yml
echo '--------------------------'

docker compose config -q
docker compose pull
docker compose up -d
docker image prune -f || true
'''

        sshagent(credentials: ['ssh-tecnogera-rsa']) {
          sh '''
            set -euxo pipefail

            # garante diretório remoto
            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} "mkdir -p ${REMOTE_DIR}"

            # envia compose e script
            scp -o StrictHostKeyChecking=no docker-compose.deploy.yml \
                tecnogera@${HOST}:${REMOTE_DIR}/docker-compose.yml
            scp -o StrictHostKeyChecking=no remote_deploy.sh \
                tecnogera@${HOST}:/tmp/remote_deploy.sh

            # executa script no host (injeta REMOTE_DIR) e remove
            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} "
              set -euxo pipefail
              chmod +x /tmp/remote_deploy.sh
              REMOTE_DIR='${REMOTE_DIR}' /tmp/remote_deploy.sh
              rm -f /tmp/remote_deploy.sh
            "
          '''
        }
      }
    }

    stage('Wait for health & Smoke') {
      steps {
        // script remoto de espera/health
        writeFile file: 'remote_wait.sh', text: '''#!/usr/bin/env bash
set -euxo pipefail

: "${REMOTE_DIR:?REMOTE_DIR não definido}"

cd "$REMOTE_DIR"

CID=$(docker compose ps -q n1agent || true)
if [ -z "$CID" ]; then
  echo "Container ID não encontrado"
  docker compose ps
  exit 1
fi

echo 'Aguardando health=healthy...'
i=0
until [ $i -ge 45 ]; do
  st=$(docker inspect -f '{{.State.Health.Status}}' "$CID" 2>/dev/null || echo 'unknown')
  echo "[$i] status=$st"
  [ "$st" = "healthy" ] && break
  i=$((i+1))
  sleep 2
done

st=$(docker inspect -f '{{.State.Health.Status}}' "$CID" 2>/dev/null || echo 'unknown')
if [ "$st" != "healthy" ]; then
  echo 'Container não ficou healthy a tempo. Logs recentes:'
  docker compose logs --no-color --tail=200 n1agent || true
  exit 1
fi

# smoke HTTP local
curl -fsS http://127.0.0.1:8001/healthz

# inventário
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}" | sed -n '1,15p'
'''

        sshagent(credentials: ['ssh-tecnogera-rsa']) {
          sh '''
            set -euxo pipefail

            # envia e executa o script de espera/health
            scp -o StrictHostKeyChecking=no remote_wait.sh \
                tecnogera@${HOST}:/tmp/remote_wait.sh

            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} "
              set -euxo pipefail
              chmod +x /tmp/remote_wait.sh
              REMOTE_DIR='${REMOTE_DIR}' /tmp/remote_wait.sh
              rm -f /tmp/remote_wait.sh
            "
          '''
        }
      }
    }
  }

  post { always { echo 'Pipeline finalizado' } }
}
