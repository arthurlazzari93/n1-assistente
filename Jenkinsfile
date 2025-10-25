pipeline {
  agent any
  environment {
    DOCKER_IMAGE = "tecnogera/n1agent"
    TAG = "${env.BUILD_NUMBER}"
  }
  stages {
    stage('Checkout') {
      steps {
        // O SCM (config. do job) já usa a credencial Username/Password (github-https ou github-pat)
        checkout scm
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
                ssh -o StrictHostKeyChecking=no tecnogera@10.246.200.14 /bin/bash -se <<'REMOTE'
                set -euxo pipefail

                mkdir -p /opt/apps/n1agent

                cat > /opt/apps/n1agent/docker-compose.yml <<YML
                services:
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
                    - "127.0.0.1:8001:8001"
                YML

                cd /opt/apps/n1agent
                docker compose pull
                docker compose up -d
                docker image prune -f || true
                REMOTE
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
