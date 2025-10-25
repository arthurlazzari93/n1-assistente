pipeline {
  agent any
  environment {
    DOCKER_IMAGE = "tecnogera/n1agent"
    TAG = "${env.BUILD_NUMBER}"          // você pode trocar para o curto do git: ${env.GIT_COMMIT[0..6]}
  }
  stages {
    stage('Checkout') {
      steps {
        withCredentials([string(credentialsId: 'github-pat', variable: 'GITHUB_PAT')]) {
          // Clona via HTTPS usando PAT (sem dor com chaves)
          sh '''
            set -e
            REPO_URL="$(git config --get remote.origin.url || true)"
            if [ -z "$REPO_URL" ]; then
              echo "[INFO] Defina o URL do repositório nas configurações do job ou use o Multibranch Pipeline."
            fi
          '''
          checkout scm
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
        sshagent(credentials: ['ssh-tecnogera-rsa']) {
          sh """
            set -euxo pipefail
            ssh -o StrictHostKeyChecking=no tecnogera@10.246.200.14 '
              set -euxo pipefail
              mkdir -p /opt/apps/n1agent
              cd /opt/apps/n1agent

              # grava/atualiza compose (produção simples)
              cat > docker-compose.yml <<EOF
              services:
                n1agent:
                  image: ${DOCKER_IMAGE}:${TAG}
                  env_file: .env
                  restart: unless-stopped
                  healthcheck:
                    test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8001/healthz"]
                    interval: 20s
                    timeout: 5s
                    retries: 5
                  ports:
                    - "127.0.0.1:8001:8001"
              EOF

              # pull + up
              docker compose pull
              docker compose up -d
              docker image prune -f || true
            '
          """
        }
      }
    }

    stage('Smoke') {
      steps {
        sshagent(credentials: ['ssh-tecnogera-rsa']) {
          sh '''
            set -e
            ssh -o StrictHostKeyChecking=no tecnogera@10.246.200.14 "curl -fsS http://127.0.0.1:8001/healthz && docker ps --format 'table {{.Names}}\\t{{.Ports}}\\t{{.Status}}' | sed -n '1,15p'"
          '''
        }
      }
    }
  }
}
