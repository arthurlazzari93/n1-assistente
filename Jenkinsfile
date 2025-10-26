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
      steps { checkout scm }
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
        // gera compose no workspace
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

        // envia por scp e faz o up no host (normaliza, valida, sobe)
        sshagent(credentials: ['ssh-tecnogera-rsa']) {
          sh '''
            set -euxo pipefail
            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} "mkdir -p ${REMOTE_DIR}"
            scp -o StrictHostKeyChecking=no docker-compose.deploy.yml tecnogera@${HOST}:${REMOTE_DIR}/docker-compose.yml

            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} /bin/bash -se <<'REMOTE'
            set -euxo pipefail
            cd ${REMOTE_DIR}

            # normalizar: remover BOM e CRLF (se houver)
            sed -i '1s/^\xEF\xBB\xBF//' docker-compose.yml || true
            sed -i 's/\r$//' docker-compose.yml || true

            echo '--- docker-compose.yml (debug) ---'
            sed -n '1,80p' docker-compose.yml
            echo '----------------------------------'

            docker compose config -q
            docker compose pull
            docker compose up -d
            docker image prune -f || true
            REMOTE
          '''
        }
      }
    }

    stage('Wait for health & Smoke') {
      steps {
        sshagent(credentials: ['ssh-tecnogera-rsa']) {
          sh '''
            set -euxo pipefail
            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} /bin/bash -se <<'REMOTE'
            set -euxo pipefail
            cd ${REMOTE_DIR}

            CID=$(docker compose ps -q n1agent || true)
            if [ -z "$CID" ]; then
              echo 'Container ID não encontrado'; docker compose ps; exit 1
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

            curl -fsS http://127.0.0.1:8001/healthz
            docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}" | sed -n '1,15p'
            REMOTE
          '''
        }
      }
    }
  }

  post { always { echo 'Pipeline finalizado' } }
}
