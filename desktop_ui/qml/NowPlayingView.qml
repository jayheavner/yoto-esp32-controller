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
            
            // Large card artwork
            Item {
                width: 280
                height: 280
                anchors.horizontalCenter: parent.horizontalCenter
                
                Image {
                    id: cardArtwork
                    anchors.centerIn: parent
                    width: Math.min(parent.width, sourceSize.width > 0 ? sourceSize.width : parent.width)
                    height: Math.min(parent.height, sourceSize.height > 0 ? sourceSize.height : parent.height)
                    source: root.cardImagePath
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
            
            // Current chapter info
            Rectangle {
                width: parent.width
                height: 80
                color: "#222"
                radius: 12
                anchors.horizontalCenter: parent.horizontalCenter
                
                Row {
                    anchors.fill: parent
                    anchors.margins: 15
                    spacing: 15
                    
                    // Chapter artwork/icon
                    Rectangle {
                        width: 50
                        height: 50
                        anchors.verticalCenter: parent.verticalCenter
                        color: "#444"
                        radius: 8
                        
                        Image {
                            anchors.fill: parent
                            anchors.margins: 2
                            source: root.currentChapter ? root.currentChapter.iconUrl : ""
                            fillMode: Image.PreserveAspectFit
                            
                            Rectangle {
                                anchors.fill: parent
                                color: "#666"
                                radius: 6
                                visible: parent.status !== Image.Ready
                                
                                Text {
                                    anchors.centerIn: parent
                                    text: root.currentChapter ? root.currentChapter.key : "01"
                                    color: "#fff"
                                    font.pixelSize: 12
                                    font.bold: true
                                }
                            }
                        }
                    }
                    
                    // Chapter title and info
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - 80
                        spacing: 5
                        
                        Text {
                            text: root.currentChapter ? root.currentChapter.title : "Chapter 1"
                            color: "#fff"
                            font.pixelSize: 18
                            font.bold: true
                            wrapMode: Text.WordWrap
                            width: parent.width
                        }
                        
                        Text {
                            text: root.cardTitle
                            color: "#aaa"
                            font.pixelSize: 14
                            wrapMode: Text.WordWrap
                            width: parent.width
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
                        console.log("Previous chapter clicked")
                    }
                }
                
                // Play/Pause Button
                Button {
                    id: playPauseButton
                    width: 100
                    height: 100
                    
                    property bool isPlaying: true  // Start as playing since we came from play button
                    
                    background: Rectangle {
                        color: playPauseButton.pressed ? "#555" : "#444"
                        radius: 50
                        border.color: "#777"
                        border.width: 3
                    }
                    
                    // Play/Pause icon
                    Canvas {
                        anchors.centerIn: parent
                        width: 40
                        height: 40
                        
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.fillStyle = "#fff"
                            ctx.beginPath()
                            
                            if (playPauseButton.isPlaying) {
                                // Pause icon (two rectangles)
                                ctx.fillRect(12, 8, 6, 24)
                                ctx.fillRect(22, 8, 6, 24)
                            } else {
                                // Play triangle icon
                                ctx.moveTo(12, 8)
                                ctx.lineTo(32, 20)
                                ctx.lineTo(12, 32)
                                ctx.closePath()
                                ctx.fill()
                            }
                        }
                        
                        // Redraw when state changes
                        Connections {
                            target: playPauseButton
                            function onIsPlayingChanged() {
                                parent.requestPaint()
                            }
                        }
                    }
                    
                    onClicked: {
                        isPlaying = !isPlaying
                        console.log("Play/Pause clicked - now", isPlaying ? "playing" : "paused")
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
                        console.log("Next chapter clicked")
                    }
                }
            }
        }
    }
}