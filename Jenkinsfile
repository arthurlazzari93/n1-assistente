pipeline {
  agent any
  environment {
    DOCKER_IMAGE = "tecnogera/n1agent"
    TAG = "${env.BUILD_NUMBER}"
    HOST = "10.246.200.14"
    REMOTE_DIR = "/opt/apps/n1agent"
    PORT_BIND = "127.0.0.1:8001:8001"
  }

  stages {
    stage('Checkout') {
      steps {
        // usa a credencial Username/Password configurada no SCM do job
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

        // copia e aplica no host
        sshagent(credentials: ['ssh-tecnogera-rsa']) {
          sh '''
            set -euxo pipefail

            # garante diretório remoto
            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} "mkdir -p ${REMOTE_DIR}"

            # envia compose
            scp -o StrictHostKeyChecking=no docker-compose.deploy.yml tecnogera@${HOST}:${REMOTE_DIR}/docker-compose.yml

            # normaliza e valida YAML no host, depois deploy
            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} "
              set -euxo pipefail
              cd ${REMOTE_DIR}

              # normalizar: remover BOM e CRLF (se houver)
              # remove BOM
              sed -i '1s/^\\xEF\\xBB\\xBF//' docker-compose.yml || true
              # remove CRLF
              sed -i 's/\\r$//' docker-compose.yml || true

              echo '--- docker-compose.yml (debug) ---'
              sed -n '1,80p' docker-compose.yml
              echo '----------------------------------'

              # valida sintaxe do compose
              docker compose config -q

              # pull + up
              docker compose pull
              docker compose up -d

              # limpeza de imagens soltas
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
            set -euxo pipefail
            # health local
            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} "curl -fsS http://127.0.0.1:8001/healthz"
            # inventário rápido
            ssh -o StrictHostKeyChecking=no tecnogera@${HOST} "docker ps --format \\"table {{.Names}}\\t{{.Ports}}\\t{{.Status}}\\" | sed -n '1,15p'"
          '''
        }
      }
    }
  }

  post {
    always {
      echo 'Pipeline finalizado'
    }
  }
}
