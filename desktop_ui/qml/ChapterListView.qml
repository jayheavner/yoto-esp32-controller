import QtQuick 6.7
import QtQuick.Controls 6.7

Item {
    id: root
    
    // Properties passed from DetailView
    property string cardId: ""
    property string cardTitle: "Unknown Card"
    property var chapters: []
    
    // Load real chapter data when component is ready
    Component.onCompleted: {
        if (cardId && coordinator) {
            console.log("Loading chapters for card:", cardId)
            chapters = coordinator.get_chapters(cardId)
            console.log("Loaded", chapters.length, "chapters")
        }
    }
    
    Rectangle {
        anchors.fill: parent
        color: "#111"
        
        // Back button - top left (child-friendly size)
        Button {
            id: backButton
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.margins: 15
            width: 70
            height: 50
            
            background: Rectangle {
                color: backButton.pressed ? "#444" : "#333"
                radius: 8
            }
            
            // Left arrow icon
            Canvas {
                id: backArrowCanvas
                anchors.centerIn: parent
                width: 30
                height: 25
                
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.strokeStyle = "#fff"
                    ctx.lineWidth = 3
                    ctx.beginPath()
                    ctx.moveTo(20, 5)
                    ctx.lineTo(8, 12.5)
                    ctx.lineTo(20, 20)
                    ctx.stroke()
                }
            }
            
            onClicked: {
                console.log("Chapter list back button clicked")
                var stackView = root.parent
                if (stackView) {
                    stackView.pop()
                }
            }
        }
        
        // Title text
        Text {
            id: titleText
            anchors.top: parent.top
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.topMargin: 30
            text: "Chapters"
            color: "#fff"
            font.pixelSize: 24
            font.bold: true
        }
        
        // Main content area
        Item {
            anchors.fill: parent
            anchors.topMargin: 80
            anchors.bottomMargin: 20
            anchors.leftMargin: 20
            anchors.rightMargin: 20
            
            // Two-column grid for chapters
            GridView {
                anchors.fill: parent
                cellWidth: parent.width / 2
                cellHeight: 120  // Child-friendly height
                model: root.chapters
                
                delegate: Rectangle {
                    width: GridView.view.cellWidth - 10
                    height: GridView.view.cellHeight - 10
                    color: chapterMouseArea.pressed ? "#444" : "#333"
                    radius: 8
                    border.color: "#555"
                    border.width: 1
                    
                    Row {
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 10
                        
                        // Chapter icon
                        Rectangle {
                            width: 60
                            height: 60
                            anchors.verticalCenter: parent.verticalCenter
                            color: "#555"
                            radius: 8
                            
                            Image {
                                anchors.fill: parent
                                anchors.margins: 4
                                source: modelData.iconUrl || ""
                                fillMode: Image.PreserveAspectFit
                                
                                // Fallback when image fails to load
                                Rectangle {
                                    anchors.fill: parent
                                    color: "#777"
                                    radius: 4
                                    visible: parent.status !== Image.Ready
                                    
                                    Text {
                                        anchors.centerIn: parent
                                        text: modelData.key
                                        color: "#fff"
                                        font.pixelSize: 14
                                        font.bold: true
                                    }
                                }
                            }
                        }
                        
                        // Chapter info
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - 80  // Remaining space after icon
                            spacing: 5
                            
                            Text {
                                text: modelData.title
                                color: "#fff"
                                font.pixelSize: 16
                                font.bold: true
                                wrapMode: Text.WordWrap
                                width: parent.width
                            }
                            
                            Text {
                                text: Math.floor(modelData.duration / 60) + ":" + 
                                      String(modelData.duration % 60).padStart(2, '0')
                                color: "#aaa"
                                font.pixelSize: 12
                            }
                        }
                    }
                    
                    // Make chapter clickable
                    MouseArea {
                        id: chapterMouseArea
                        anchors.fill: parent
                        onClicked: {
                            console.log("Chapter clicked:", modelData.title, "Key:", modelData.key)
                            
                            // Navigate to Now Playing with this specific chapter
                            var stackView = root.parent
                            if (stackView && window.selectedCard) {
                                stackView.push("NowPlayingView.qml", {
                                    "cardId": root.cardId,
                                    "cardTitle": root.cardTitle,
                                    "cardImagePath": window.selectedCard.imagePath,
                                    "activeChapter": modelData
                                })
                            }
                        }
                    }
                }
            }
            
            // Empty state message (shown when no chapters)
            Text {
                anchors.centerIn: parent
                text: "No chapters available"
                color: "#666"
                font.pixelSize: 18
                visible: root.chapters.length === 0
            }
        }
    }
}