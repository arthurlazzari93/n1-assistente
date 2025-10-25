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
        // 1) Gerar o arquivo docker-compose.yml no workspace (com a tag da imagem)
        writeFile file: 'docker-compose.deploy.yml', text: """
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
    """

        // 2) Enviar o compose e aplicar no host
        sshagent(credentials: ['ssh-tecnogera-rsa']) {
        sh '''
            set -euxo pipefail

            # Garante a pasta no host
            ssh -o StrictHostKeyChecking=no tecnogera@10.246.200.14 "mkdir -p /opt/apps/n1agent"

            # Copia o compose gerado
            scp -o StrictHostKeyChecking=no docker-compose.deploy.yml tecnogera@10.246.200.14:/opt/apps/n1agent/docker-compose.yml

            # Pull + Up
            ssh -o StrictHostKeyChecking=no tecnogera@10.246.200.14 "
            set -euxo pipefail
            cd /opt/apps/n1agent
            docker compose pull
            docker compose up -d
            docker image prune -f || true
            "
        '''
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
