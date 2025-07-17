import QtQuick 6.7
import QtQuick.Controls 6.7

Item {
    id: root
    
    property string cardId: ""
    property string cardTitle: "Unknown Card"
    property string cardImagePath: ""
    property var activeChapter: null  // Can be passed in from ChapterListView
    property var currentChapter: null  // The chapter to display (either activeChapter or first chapter)
    
    // Load appropriate chapter when component is ready
    Component.onCompleted: {
        if (activeChapter) {
            // Use the specific chapter passed in (from ChapterListView)
            currentChapter = activeChapter
            console.log("Using passed chapter:", currentChapter.title)
        } else if (cardId && coordinator) {
            // Load first chapter as fallback (from DetailView play button)
            console.log("Loading first chapter for card:", cardId)
            var chapters = coordinator.get_chapters(cardId)
            if (chapters && chapters.length > 0) {
                currentChapter = chapters[0]
                console.log("Loaded first chapter:", currentChapter.title)
            }
        }
    }
    
    Rectangle {
        anchors.fill: parent
        color: "#111"
        
        // Home button - top left
        Button {
            id: homeButton
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.margins: 15
            width: 70
            height: 50
            
            background: Rectangle {
                color: homeButton.pressed ? "#444" : "#333"
                radius: 8
            }
            
            // Home icon
            Canvas {
                id: homeCanvas
                anchors.centerIn: parent
                width: 30
                height: 25
                
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.strokeStyle = "#fff"
                    ctx.lineWidth = 3
                    ctx.beginPath()
                    // Simple house icon
                    ctx.moveTo(8, 20)
                    ctx.lineTo(8, 12)
                    ctx.lineTo(15, 5)
                    ctx.lineTo(22, 12)
                    ctx.lineTo(22, 20)
                    ctx.lineTo(8, 20)
                    // Door
                    ctx.moveTo(12, 20)
                    ctx.lineTo(12, 15)
                    ctx.lineTo(18, 15)
                    ctx.lineTo(18, 20)
                    ctx.stroke()
                }
            }
            
            onClicked: {
                console.log("Home button clicked - returning to main grid")
                var stackView = root.parent
                if (stackView) {
                    // Pop all the way back to the main grid
                    stackView.clear()
                    stackView.push(stackView.initialItem)
                }
            }
        }
        
        // Main content area
        Column {
            anchors.centerIn: parent
            spacing: 40
            width: parent.width - 40
            
            // Large card artwork - use MQTT-derived image if available
            Item {
                width: 280
                height: 280
                anchors.horizontalCenter: parent.horizontalCenter
                
                Image {
                    id: cardArtwork
                    anchors.centerIn: parent
                    width: Math.min(parent.width, sourceSize.width > 0 ? sourceSize.width : parent.width)
                    height: Math.min(parent.height, sourceSize.height > 0 ? sourceSize.height : parent.height)
                    // Prefer MQTT-derived image, fallback to passed image
                    source: coordinator.activeCardImagePath || root.cardImagePath
                    fillMode: Image.PreserveAspectFit
                    
                    Rectangle {
                        anchors.fill: parent
                        color: "#444"
                        visible: cardArtwork.status !== Image.Ready
                        radius: 12
                        
                        Text {
                            anchors.centerIn: parent
                            text: "Card\nArtwork"
                            color: "#888"
                            font.pixelSize: 20
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }
            }
            
            // Current chapter info - use MQTT data when available
            Rectangle {
                width: parent.width
                height: 120  // Taller for position info
                color: "#222"
                radius: 12
                anchors.horizontalCenter: parent.horizontalCenter
                
                Column {
                    anchors.fill: parent
                    anchors.margins: 15
                    spacing: 8
                    
                    // Chapter title - prefer MQTT data
                    Text {
                        text: coordinator.currentChapterTitle || 
                              (root.currentChapter ? root.currentChapter.title : "Chapter 1")
                        color: "#fff"
                        font.pixelSize: 18
                        font.bold: true
                        wrapMode: Text.WordWrap
                        width: parent.width
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                    
                    // Card title
                    Text {
                        text: coordinator.currentCardTitle || root.cardTitle
                        color: "#aaa"
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                        width: parent.width
                        maximumLineCount: 1
                        elide: Text.ElideRight
                    }
                    
                    // Progress bar and time info (only show if we have MQTT data)
                    Item {
                        width: parent.width
                        height: 30
                        visible: coordinator.currentTrackLength > 0
                        
                        // Progress bar background
                        Rectangle {
                            id: progressBackground
                            width: parent.width
                            height: 4
                            anchors.verticalCenter: parent.verticalCenter
                            color: "#444"
                            radius: 2
                            
                            // Progress fill
                            Rectangle {
                                width: coordinator.currentTrackLength > 0 ? 
                                       (coordinator.currentTrackPosition / coordinator.currentTrackLength) * parent.width : 0
                                height: parent.height
                                color: "#00ff88"
                                radius: 2
                                
                                Behavior on width {
                                    NumberAnimation { duration: 200 }
                                }
                            }
                        }
                        
                        // Time display
                        Row {
                            anchors.top: progressBackground.bottom
                            anchors.topMargin: 8
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: 10
                            
                            Text {
                                text: coordinator.formattedPosition
                                color: "#aaa"
                                font.pixelSize: 12
                            }
                            
                            Text {
                                text: "/"
                                color: "#666"
                                font.pixelSize: 12
                            }
                            
                            Text {
                                text: coordinator.formattedDuration
                                color: "#aaa"
                                font.pixelSize: 12
                            }
                        }
                    }
                }
            }
            
            // Transport controls
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 30
                
                // Previous Chapter Button
                Button {
                    id: prevButton
                    width: 80
                    height: 80
                    
                    background: Rectangle {
                        color: prevButton.pressed ? "#555" : "#333"
                        radius: 40
                        border.color: "#666"
                        border.width: 2
                    }
                    
                    // Previous chapter icon (⏮)
                    Canvas {
                        id: prevCanvas
                        anchors.centerIn: parent
                        width: 40
                        height: 30
                        
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.fillStyle = "#fff"
                            ctx.beginPath()
                            
                            // Vertical line (stop)
                            ctx.fillRect(8, 8, 4, 14)
                            
                            // Triangle pointing left
                            ctx.moveTo(30, 8)
                            ctx.lineTo(16, 15)
                            ctx.lineTo(30, 22)
                            ctx.closePath()
                            ctx.fill()
                        }
                    }
                    
                    onClicked: {
                        coordinator.previous_track()
                    }
                }
                
                // Play/Pause Button - connected to coordinator state
                Button {
                    id: playPauseButton
                    width: 100
                    height: 100
                    
                    background: Rectangle {
                        color: playPauseButton.pressed ? "#555" : "#444"
                        radius: 50
                        border.color: "#777"
                        border.width: 3
                    }
                    
                    // Play/Pause icon - shows opposite action based on coordinator state
                    Canvas {
                        id: playPauseCanvas
                        anchors.centerIn: parent
                        width: 40
                        height: 40
                        
                        onPaint: {
                            var ctx = getContext("2d")
                            // Clear the canvas first
                            ctx.clearRect(0, 0, width, height)
                            
                            ctx.fillStyle = "#fff"
                            ctx.beginPath()
                            
                            if (coordinator.isPlaying) {
                                // Show pause icon (two rectangles) when playing
                                ctx.fillRect(12, 8, 6, 24)
                                ctx.fillRect(22, 8, 6, 24)
                            } else {
                                // Show play triangle icon when stopped/paused
                                ctx.moveTo(12, 8)
                                ctx.lineTo(32, 20)
                                ctx.lineTo(12, 32)
                                ctx.closePath()
                                ctx.fill()
                            }
                        }
                        
                        // Redraw when coordinator state changes
                        Connections {
                            target: coordinator
                            function onPlaybackStateChanged() {
                                playPauseCanvas.requestPaint()
                            }
                        }
                    }
                    
                    onClicked: {
                        coordinator.toggle_play_pause()
                    }
                }
                
                // Next Chapter Button
                Button {
                    id: nextButton
                    width: 80
                    height: 80
                    
                    background: Rectangle {
                        color: nextButton.pressed ? "#555" : "#333"
                        radius: 40
                        border.color: "#666"
                        border.width: 2
                    }
                    
                    // Next chapter icon (⏭)
                    Canvas {
                        id: nextCanvas
                        anchors.centerIn: parent
                        width: 40
                        height: 30
                        
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.fillStyle = "#fff"
                            ctx.beginPath()
                            
                            // Triangle pointing right
                            ctx.moveTo(10, 8)
                            ctx.lineTo(24, 15)
                            ctx.lineTo(10, 22)
                            ctx.closePath()
                            ctx.fill()
                            
                            // Vertical line (stop)
                            ctx.fillRect(28, 8, 4, 14)
                        }
                    }
                    
                    onClicked: {
                        coordinator.next_track()
                    }
                }
            }
        }
    }
}